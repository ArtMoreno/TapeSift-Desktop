"""Manage the six result shortcuts and the complete result vocabulary."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QInputDialog, QLineEdit, QListWidget, QMessageBox, QPushButton,
    QVBoxLayout, QWidget,
)


from tapesift.services.football_vocab import values_for

STANDARD_RESULT_CHOICES = tuple(values_for("result"))
MAX_RESULT_FAVORITES = 6


def unique_results(*groups: list[str] | tuple[str, ...]) -> list[str]:
    """Return clean, case-insensitively unique values in stable order."""
    values: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for value in group:
            cleaned = str(value).strip()
            key = cleaned.casefold()
            if cleaned and key not in seen:
                seen.add(key)
                values.append(cleaned)
    return values


class ResultManagerDialog(QDialog):
    """Edit visible favorites without losing the larger result library."""

    def __init__(
            self, favorites: list[str], results: list[str],
            parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ResultManagerDialog")
        self.setWindowTitle("Manage Results")
        self.resize(640, 500)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(10)
        intro = QLabel(
            "Keep up to six results on the Play Details panel. Everything "
            "else remains available under More Results.")
        intro.setWordWrap(True)
        intro.setProperty("role", "muted")
        outer.addWidget(intro)

        columns = QHBoxLayout()
        favorite_column = QVBoxLayout()
        favorite_heading = QLabel("FAST RESULTS (MAX 6)")
        favorite_heading.setProperty("role", "eyebrow")
        favorite_column.addWidget(favorite_heading)
        self.favorite_list = QListWidget()
        self.favorite_list.setObjectName("ResultFavoritesList")
        self.favorite_list.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove)
        self.favorite_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.favorite_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        favorite_column.addWidget(self.favorite_list, 1)
        remove_favorite = QPushButton("Remove from fast results")
        remove_favorite.clicked.connect(self._remove_favorite)
        favorite_column.addWidget(remove_favorite)
        columns.addLayout(favorite_column, 1)

        library_column = QVBoxLayout()
        library_heading = QLabel("ALL RESULTS")
        library_heading.setProperty("role", "eyebrow")
        library_column.addWidget(library_heading)
        self.result_list = QListWidget()
        self.result_list.setObjectName("ResultLibraryList")
        self.result_list.itemDoubleClicked.connect(self._add_selected_favorite)
        library_column.addWidget(self.result_list, 1)
        add_favorite = QPushButton("Add to fast results")
        add_favorite.clicked.connect(self._add_selected_favorite)
        library_column.addWidget(add_favorite)
        remove_result = QPushButton("Remove custom result")
        remove_result.clicked.connect(self._remove_selected_result)
        library_column.addWidget(remove_result)
        rename_result = QPushButton("Rename custom result")
        rename_result.clicked.connect(self._rename_selected_result)
        library_column.addWidget(rename_result)
        columns.addLayout(library_column, 1)
        outer.addLayout(columns, 1)

        custom_row = QHBoxLayout()
        self.custom_edit = QLineEdit()
        self.custom_edit.setPlaceholderText("Add a custom football result")
        self.custom_edit.returnPressed.connect(self._add_custom)
        custom_row.addWidget(self.custom_edit, 1)
        add_custom = QPushButton("Add Result")
        add_custom.clicked.connect(self._add_custom)
        custom_row.addWidget(add_custom)
        outer.addLayout(custom_row)

        reset_row = QHBoxLayout()
        reset = QPushButton("Restore standard results")
        reset.clicked.connect(self._restore_standards)
        reset_row.addWidget(reset)
        reset_row.addStretch(1)
        outer.addLayout(reset_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        for value in unique_results(favorites)[:MAX_RESULT_FAVORITES]:
            self.favorite_list.addItem(value)
        for value in unique_results(
                STANDARD_RESULT_CHOICES, results, favorites):
            self.result_list.addItem(value)

    def _add_selected_favorite(self, *_args) -> None:
        item = self.result_list.currentItem()
        if item is None:
            return
        value = item.text()
        existing = {
            self.favorite_list.item(index).text().casefold()
            for index in range(self.favorite_list.count())
        }
        if value.casefold() in existing:
            return
        if self.favorite_list.count() >= MAX_RESULT_FAVORITES:
            QMessageBox.information(
                self, "Fast Results",
                "Remove one fast result before adding another.")
            return
        self.favorite_list.addItem(value)

    def _remove_favorite(self) -> None:
        row = self.favorite_list.currentRow()
        if row >= 0:
            self.favorite_list.takeItem(row)

    def _add_custom(self) -> None:
        value = self.custom_edit.text().strip()
        if not value:
            return
        existing = {
            self.result_list.item(index).text().casefold()
            for index in range(self.result_list.count())
        }
        if value.casefold() not in existing:
            self.result_list.addItem(value)
        self.custom_edit.clear()

    def _remove_selected_result(self) -> None:
        row = self.result_list.currentRow()
        item = self.result_list.item(row)
        if item is None:
            return
        value = item.text()
        standards = {result.casefold() for result in STANDARD_RESULT_CHOICES}
        if value.casefold() in standards:
            QMessageBox.information(
                self, "Result Library",
                "Standard football results stay in the library.")
            return
        favorites = {
            self.favorite_list.item(index).text().casefold()
            for index in range(self.favorite_list.count())
        }
        if value.casefold() in favorites:
            QMessageBox.information(
                self, "Result Library",
                "Remove this result from Fast Results first.")
            return
        self.result_list.takeItem(row)

    def _rename_selected_result(self) -> None:
        item = self.result_list.currentItem()
        if item is None:
            return
        old_value = item.text()
        standards = {result.casefold() for result in STANDARD_RESULT_CHOICES}
        if old_value.casefold() in standards:
            QMessageBox.information(
                self, "Result Library",
                "Standard football results cannot be renamed.")
            return
        value, accepted = QInputDialog.getText(
            self, "Rename Result", "Result name:", text=old_value)
        value = value.strip()
        if not accepted or not value:
            return
        existing = {
            self.result_list.item(index).text().casefold()
            for index in range(self.result_list.count())
            if self.result_list.item(index) is not item
        }
        if value.casefold() in existing:
            QMessageBox.information(
                self, "Result Library", "That result already exists.")
            return
        item.setText(value)
        for index in range(self.favorite_list.count()):
            favorite = self.favorite_list.item(index)
            if favorite.text().casefold() == old_value.casefold():
                favorite.setText(value)

    def _restore_standards(self) -> None:
        current = self.results()
        self.result_list.clear()
        for value in unique_results(STANDARD_RESULT_CHOICES, current):
            self.result_list.addItem(value)

    def favorites(self) -> list[str]:
        return [
            self.favorite_list.item(index).text()
            for index in range(self.favorite_list.count())
        ][:MAX_RESULT_FAVORITES]

    def results(self) -> list[str]:
        return [
            self.result_list.item(index).text()
            for index in range(self.result_list.count())
        ]
