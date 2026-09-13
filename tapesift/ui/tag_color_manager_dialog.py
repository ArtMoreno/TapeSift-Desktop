"""Project-level presentation settings for timeline tag colors."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QDialog, QDialogButtonBox, QHeaderView,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from tapesift.services.tag_style_service import (
    DEFAULT_STYLE, canonical_tag_key, copy_styles, normalized_styles,
    style_for_tag,
)
from tapesift.ui.dialog_components import DialogHeader


class TagColorManagerDialog(QDialog):
    """Choose how tags appear in the Primary Tag timeline view."""

    HEADERS = ("Tag", "Color", "Category", "Primary", "Timeline")

    def __init__(
            self,
            tags: list[str],
            styles: dict[str, dict[str, object]],
            parent=None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("tapesiftDialog", True)
        self.setWindowTitle("Timeline Tag Colors")
        self.setMinimumSize(760, 540)
        self._original_styles = copy_styles(styles)
        self._rows: dict[str, dict[str, object]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(DialogHeader(
            "Timeline tag colors",
            "Control which tags lead the Primary Tag view and how they read "
            "on the timeline.",
            eyebrow="PROJECT  /  TIMELINE",
            parent=self,
        ))
        note = QLabel(
            "Primary tags color a play when Timeline Color by is set to "
            "Primary Tag. Secondary or hidden tags stay on the clip and in "
            "search, but do not control that timeline block.")
        note.setProperty("role", "subtle")
        note.setWordWrap(True)
        layout.addWidget(note)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(10)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter tags by name or category")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.setAccessibleName("Filter timeline tags")
        filter_row.addWidget(self.filter_edit, 1)
        self.filter_count = QLabel("")
        self.filter_count.setProperty("role", "subtle")
        filter_row.addWidget(self.filter_count)
        layout.addLayout(filter_row)

        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 1)

        unique_tags: dict[str, str] = {}
        for tag in tags:
            clean = " ".join(tag.strip().split())
            key = canonical_tag_key(clean)
            if key and key not in unique_tags:
                unique_tags[key] = clean
        configured = set(self._original_styles)
        for tag in sorted(
                unique_tags.values(),
                key=lambda value: (
                    canonical_tag_key(value) not in configured,
                    value.casefold(),
                )):
            self._add_row(tag)
        self.filter_edit.textChanged.connect(self._apply_filter)
        self._apply_filter("")

        self.empty_label = QLabel(
            "No tags exist in this project yet. Add tags to clips, then "
            "return here to set their timeline colors.")
        self.empty_label.setProperty("role", "subtle")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setVisible(not unique_tags)
        layout.addWidget(self.empty_label)

        footer = QHBoxLayout()
        defaults = QPushButton("Restore Defaults")
        defaults.clicked.connect(self._restore_defaults)
        footer.addWidget(defaults)
        footer.addStretch()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Save tag styles")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setProperty(
            "primary", "true")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons)
        layout.addLayout(footer)

    def _add_row(self, tag: str) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        key = canonical_tag_key(tag)
        style = style_for_tag(tag, self._original_styles)
        tag_item = QTableWidgetItem(tag)
        tag_item.setData(Qt.ItemDataRole.UserRole, key)
        self.table.setItem(row, 0, tag_item)

        color_button = QPushButton()
        color_button.setMinimumWidth(84)
        color_button.setAccessibleName(f"Timeline color for {tag}")
        color_button.clicked.connect(
            lambda _checked=False, tag_key=key: self._choose_color(tag_key))
        self._set_color_button(color_button, str(style["color"]))
        self.table.setCellWidget(row, 1, color_button)

        category = QLineEdit(str(style["category"]))
        category.setPlaceholderText("Tag name")
        category.setToolTip(
            "Optional shared label. Tags with the same category use the "
            "same timeline key.")
        self.table.setCellWidget(row, 2, category)

        primary = QCheckBox()
        primary.setChecked(bool(style["primary"]))
        primary.setToolTip("Allow this tag to control Primary Tag colors")
        primary.setAccessibleName(
            f"Use {tag} as a primary timeline tag")
        self.table.setCellWidget(row, 3, self._center(primary))

        timeline = QCheckBox()
        timeline.setChecked(bool(style["show_on_timeline"]))
        timeline.setToolTip("Show this tag in the Primary Tag timeline view")
        timeline.setAccessibleName(f"Show {tag} on the timeline")
        self.table.setCellWidget(row, 4, self._center(timeline))

        self._rows[key] = {
            "tag": tag,
            "color": color_button,
            "category": category,
            "primary": primary,
            "timeline": timeline,
            "row": row,
        }

    @staticmethod
    def _center(widget) -> QWidget:
        """Keep the checkbox compact and centered in its table column."""
        wrapper = QWidget()
        row = QHBoxLayout(wrapper)
        row.setContentsMargins(0, 0, 0, 0)
        row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(widget)
        return wrapper

    @staticmethod
    def _set_color_button(button: QPushButton, color: str) -> None:
        button.setProperty("tagColor", color)
        if color:
            button.setText(color.upper())
            foreground = TagColorManagerDialog._text_color_for(color)
            button.setStyleSheet(
                f"background-color: {color}; color: {foreground}; "
                f"border-color: {color};")
        else:
            button.setText("Auto")
            button.setStyleSheet("")
        button.setToolTip(
            "Pick a custom color" if color else
            "Automatic project color. Click to choose a custom color.")

    @staticmethod
    def _text_color_for(color: str) -> str:
        """Choose the more legible dark or light label for a color chip."""
        value = QColor(color)
        if not value.isValid():
            return "#f7fbf8"

        def linear(channel: int) -> float:
            normalized = channel / 255
            return (
                normalized / 12.92 if normalized <= 0.04045
                else ((normalized + 0.055) / 1.055) ** 2.4
            )

        luminance = (
            0.2126 * linear(value.red())
            + 0.7152 * linear(value.green())
            + 0.0722 * linear(value.blue())
        )
        white_ratio = 1.05 / (luminance + 0.05)
        dark_luminance = (
            0.2126 * linear(7)
            + 0.7152 * linear(21)
            + 0.0722 * linear(13)
        )
        dark_ratio = (
            (luminance + 0.05) / (dark_luminance + 0.05)
            if luminance >= dark_luminance
            else (dark_luminance + 0.05) / (luminance + 0.05)
        )
        return "#f7fbf8" if white_ratio >= dark_ratio else "#07150d"

    def _apply_filter(self, query: str) -> None:
        """Keep large real-world tag sets easy to scan."""
        terms = query.casefold().split()
        shown = 0
        for controls in self._rows.values():
            row = int(controls["row"])
            category = controls["category"]
            assert isinstance(category, QLineEdit)
            haystack = (
                f"{controls['tag']} {category.text()}".casefold()
            )
            visible = all(term in haystack for term in terms)
            self.table.setRowHidden(row, not visible)
            shown += int(visible)
        total = len(self._rows)
        self.filter_count.setText(
            f"{shown} of {total} tags" if terms else f"{total} tags")

    def _choose_color(self, key: str) -> None:
        controls = self._rows[key]
        button = controls["color"]
        assert isinstance(button, QPushButton)
        current = QColor(str(button.property("tagColor") or "#39e07a"))
        color = QColorDialog.getColor(current, self, "Choose timeline color")
        if color.isValid():
            self._set_color_button(button, color.name())

    def _restore_defaults(self) -> None:
        for controls in self._rows.values():
            button = controls["color"]
            category = controls["category"]
            primary = controls["primary"]
            timeline = controls["timeline"]
            assert isinstance(button, QPushButton)
            assert isinstance(category, QLineEdit)
            assert isinstance(primary, QCheckBox)
            assert isinstance(timeline, QCheckBox)
            self._set_color_button(button, "")
            category.clear()
            primary.setChecked(True)
            timeline.setChecked(True)

    def tag_styles(self) -> dict[str, dict[str, object]]:
        """Return only explicit overrides, preserving untouched old entries."""
        styles = copy_styles(self._original_styles)
        for key, controls in self._rows.items():
            button = controls["color"]
            category = controls["category"]
            primary = controls["primary"]
            timeline = controls["timeline"]
            assert isinstance(button, QPushButton)
            assert isinstance(category, QLineEdit)
            assert isinstance(primary, QCheckBox)
            assert isinstance(timeline, QCheckBox)
            value = {
                "color": str(button.property("tagColor") or ""),
                "category": " ".join(category.text().split()),
                "primary": primary.isChecked(),
                "show_on_timeline": timeline.isChecked(),
            }
            if value == DEFAULT_STYLE:
                styles.pop(key, None)
            else:
                styles[key] = value
        return normalized_styles(styles)
