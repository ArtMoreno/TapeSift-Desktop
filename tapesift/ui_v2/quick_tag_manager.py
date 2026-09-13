"""Editor for the favorites shown in the V2 quick-tag rail."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from tapesift.ui_v2.quick_tag_tray import QuickTag


FIELD_LABELS = {
    "quarter": "Quarter",
    "run_pass": "Run / Pass",
    "play_type": "Play Type",
    "play_action": "Play Action",
    "action": "Action",
    "result": "Result",
}


class QuickTagDefinitionDialog(QDialog):
    """Edit a button label and the detail values it saves."""

    def __init__(
            self, tag: QuickTag, fixed_details: dict[str, list[str]],
            parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("V2QuickTagDefinitionDialog")
        self.setWindowTitle("Quick Tag")
        self.setMinimumWidth(500)
        self._tag = tag
        self._fixed_details = fixed_details

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(12)

        intro = QLabel(
            "Name the button and choose the clip details it saves in one click.")
        intro.setWordWrap(True)
        intro.setProperty("role", "muted")
        outer.addWidget(intro)

        form = QFormLayout()
        self.label_edit = QLineEdit(tag.label)
        self.label_edit.setMaxLength(22)
        self.label_edit.setPlaceholderText("Button label")
        form.addRow("Button label", self.label_edit)
        outer.addLayout(form)

        rules_heading = QLabel("SAVES THESE DETAILS")
        rules_heading.setProperty("role", "eyebrow")
        outer.addWidget(rules_heading)

        self.rules = QTableWidget(0, 2)
        self.rules.setObjectName("V2QuickTagRules")
        self.rules.setHorizontalHeaderLabels(("Field", "Value"))
        self.rules.horizontalHeader().setStretchLastSection(True)
        self.rules.verticalHeader().hide()
        # QTableWidget defaults to rows that are shorter than TapeSift's
        # styled combo boxes.  That clipped the text to a thin stripe even
        # though the dialog had plenty of vertical room.
        self.rules.verticalHeader().setDefaultSectionSize(42)
        self.rules.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.rules.setMinimumHeight(150)
        outer.addWidget(self.rules)

        rule_actions = QHBoxLayout()
        add_rule = QPushButton("Add rule")
        remove_rule = QPushButton("Remove rule")
        add_rule.clicked.connect(self._add_rule)
        remove_rule.clicked.connect(self._remove_rule)
        rule_actions.addWidget(add_rule)
        rule_actions.addWidget(remove_rule)
        rule_actions.addStretch(1)
        outer.addLayout(rule_actions)

        for field, value in tag.details:
            self._add_rule(field, value)
        if not tag.details:
            self._add_rule("play_type", "")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _add_rule(
            self, field: str = "play_type", value: str = "") -> None:
        row = self.rules.rowCount()
        self.rules.insertRow(row)

        field_combo = QComboBox()
        for key, label in FIELD_LABELS.items():
            field_combo.addItem(label, key)
        field_index = field_combo.findData(field)
        field_combo.setCurrentIndex(max(0, field_index))

        value_combo = QComboBox()
        value_combo.setEditable(True)
        value_combo.addItems(self._fixed_details.get(field, []))
        value_combo.setCurrentText(value)
        field_combo.setMinimumHeight(32)
        value_combo.setMinimumHeight(32)
        field_combo.currentIndexChanged.connect(
            lambda _index, combo=field_combo, values=value_combo:
            self._field_changed(combo, values))

        self.rules.setRowHeight(row, 42)
        self.rules.setCellWidget(row, 0, field_combo)
        self.rules.setCellWidget(row, 1, value_combo)

    def _field_changed(
            self, field_combo: QComboBox, value_combo: QComboBox) -> None:
        current = value_combo.currentText()
        value_combo.clear()
        value_combo.addItems(
            self._fixed_details.get(field_combo.currentData(), []))
        value_combo.setCurrentText(current)

    def _remove_rule(self) -> None:
        rows = sorted(
            {index.row() for index in self.rules.selectedIndexes()},
            reverse=True,
        )
        if not rows and self.rules.rowCount():
            rows = [self.rules.rowCount() - 1]
        for row in rows:
            self.rules.removeRow(row)

    def _validate_and_accept(self) -> None:
        label = self.label_edit.text().strip()
        details = self._details()
        if not label:
            QMessageBox.warning(self, "Quick Tag", "Enter a button label.")
            return
        if not details:
            QMessageBox.warning(
                self, "Quick Tag", "Add at least one field and value.")
            return
        self.accept()

    def _details(self) -> tuple[tuple[str, str], ...]:
        values: list[tuple[str, str]] = []
        seen: set[str] = set()
        for row in range(self.rules.rowCount()):
            field_combo = self.rules.cellWidget(row, 0)
            value_combo = self.rules.cellWidget(row, 1)
            if not isinstance(field_combo, QComboBox) \
                    or not isinstance(value_combo, QComboBox):
                continue
            field = str(field_combo.currentData())
            value = value_combo.currentText().strip()
            if field and value and field not in seen:
                seen.add(field)
                values.append((field, value))
        return tuple(values)

    def result_tag(self) -> QuickTag:
        details = self._details()
        summary = ", ".join(
            f"{FIELD_LABELS.get(field, field)}: {value}"
            for field, value in details)
        return replace(
            self._tag,
            label=self.label_edit.text().strip(),
            details=details,
            description=f"Save {summary}",
        )


class QuickTagManagerDialog(QDialog):
    """Add, edit, remove, and drag quick-tag favorites into order."""

    def __init__(
            self, tags: tuple[QuickTag, ...],
            fixed_details: dict[str, list[str]],
            defaults: tuple[QuickTag, ...],
            parent: QWidget | None = None,
            add_immediately: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("V2QuickTagManagerDialog")
        self.setWindowTitle("Manage Quick Tags")
        self.resize(620, 520)
        self._fixed_details = fixed_details
        self._defaults = defaults

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(12)

        heading = QLabel("QUICK TAG FAVORITES")
        heading.setProperty("role", "eyebrow")
        outer.addWidget(heading)
        copy = QLabel(
            "Drag tags into your preferred order. Add custom one-click "
            "actions or edit what an existing button saves.")
        copy.setWordWrap(True)
        copy.setProperty("role", "muted")
        outer.addWidget(copy)

        self.tag_list = QListWidget()
        self.tag_list.setObjectName("V2QuickTagManagerList")
        self.tag_list.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove)
        self.tag_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.tag_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.tag_list.itemDoubleClicked.connect(self._edit_selected)
        outer.addWidget(self.tag_list, 1)
        self._load(tags)

        actions = QHBoxLayout()
        add_button = QPushButton("Add tag")
        edit_button = QPushButton("Edit")
        remove_button = QPushButton("Remove")
        add_button.setObjectName("V2QuickTagAdd")
        add_button.clicked.connect(self._add)
        edit_button.clicked.connect(self._edit_selected)
        remove_button.clicked.connect(self._remove_selected)
        actions.addWidget(add_button)
        actions.addWidget(edit_button)
        actions.addWidget(remove_button)
        actions.addStretch(1)
        reset_button = QPushButton("Restore defaults")
        reset_button.clicked.connect(self._restore_defaults)
        actions.addWidget(reset_button)
        outer.addLayout(actions)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        if add_immediately:
            # Let the manager become visible before placing its child editor
            # on top. Opening a nested modal dialog from the constructor can
            # put it behind the main window on Windows.
            QTimer.singleShot(0, self._add)

    def _load(self, tags: tuple[QuickTag, ...]) -> None:
        self.tag_list.clear()
        for tag in tags:
            self._append(tag)
        if self.tag_list.count():
            self.tag_list.setCurrentRow(0)

    def _append(self, tag: QuickTag) -> None:
        summary = "  •  ".join(
            f"{FIELD_LABELS.get(field, field)} = {value}"
            for field, value in tag.details)
        item = QListWidgetItem(f"{tag.label}    {summary}")
        item.setData(Qt.ItemDataRole.UserRole, tag)
        item.setToolTip("Double-click to edit")
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsDragEnabled)
        self.tag_list.addItem(item)

    def _add(self) -> None:
        tag = QuickTag(
            f"custom_{uuid4().hex[:12]}", "",
            (("play_type", ""),), "Custom quick tag")
        editor = QuickTagDefinitionDialog(
            tag, self._fixed_details, self)
        if editor.exec() == QDialog.DialogCode.Accepted:
            self._append(editor.result_tag())
            self.tag_list.setCurrentRow(self.tag_list.count() - 1)

    def _edit_selected(self, _item: QListWidgetItem | None = None) -> None:
        item = self.tag_list.currentItem()
        if item is None:
            return
        editor = QuickTagDefinitionDialog(
            item.data(Qt.ItemDataRole.UserRole),
            self._fixed_details,
            self,
        )
        if editor.exec() == QDialog.DialogCode.Accepted:
            tag = editor.result_tag()
            item.setData(Qt.ItemDataRole.UserRole, tag)
            summary = "  •  ".join(
                f"{FIELD_LABELS.get(field, field)} = {value}"
                for field, value in tag.details)
            item.setText(f"{tag.label}    {summary}")

    def _remove_selected(self) -> None:
        row = self.tag_list.currentRow()
        if row >= 0:
            self.tag_list.takeItem(row)

    def _restore_defaults(self) -> None:
        self._load(self._defaults)

    def tags(self) -> tuple[QuickTag, ...]:
        return tuple(
            self.tag_list.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(self.tag_list.count())
        )
