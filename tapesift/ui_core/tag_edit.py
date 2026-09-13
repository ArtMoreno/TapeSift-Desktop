"""Line edit with autocomplete for comma-separated tags.

Completes only the tag currently being typed (the text after the last comma),
so "coverage, Nic" suggests "Nickel" without clobbering earlier tags.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QStringListModel, Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import QComboBox, QCompleter, QLineEdit, QMenu

from tapesift.services.result_service import join_results, split_results


class AddTagCombo(QComboBox):
    """Compact 'Add tag ▼' dropdown that appends a fixed tag to a TagLineEdit."""

    def __init__(self, target: "TagLineEdit", parent=None) -> None:
        super().__init__(parent)
        self._target = target
        self.setToolTip("Add a tag from the fixed list")
        self.addItem("Add tag…")
        self.activated.connect(self._picked)

    def set_fixed_tags(self, tags: list[str]) -> None:
        self.blockSignals(True)
        self.clear()
        self.addItem("Add tag…")
        self.addItems(tags)
        self.blockSignals(False)

    def _picked(self, index: int) -> None:
        if index <= 0:
            return
        tag = self.itemText(index)
        current = [t.strip() for t in self._target.text().split(",") if t.strip()]
        if tag.lower() not in {t.lower() for t in current}:
            current.append(tag)
            self._target.setText(", ".join(current))
        self.setCurrentIndex(0)


class TagLineEdit(QLineEdit):
    def __init__(self, known_tags: list[str] | None = None, parent=None) -> None:
        super().__init__(parent)
        self._model = QStringListModel(sorted(known_tags or []))
        self._completer = QCompleter(self._model, self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._completer.setWidget(self)
        self._completer.activated.connect(self._insert_completion)
        self.textEdited.connect(self._on_text_edited)

    def set_known_tags(self, tags: list[str]) -> None:
        self._model.setStringList(sorted(set(tags)))

    def _current_token(self) -> str:
        return self.text().rpartition(",")[2].strip()

    def _on_text_edited(self, _text: str) -> None:
        token = self._current_token()
        if len(token) >= 1:
            self._completer.setCompletionPrefix(token)
            if self._completer.completionCount():
                self._completer.complete()
                return
        self._completer.popup().hide()

    def _insert_completion(self, completion: str) -> None:
        head, sep, _tail = self.text().rpartition(",")
        if sep:
            self.setText(f"{head.rstrip()}, {completion}")
        else:
            self.setText(completion)
        self.setCursorPosition(len(self.text()))


class AutoCompleteLineEdit(QLineEdit):
    """Single-value line edit with contains-matching autocomplete."""

    def __init__(self, known_values: list[str] | None = None, parent=None) -> None:
        super().__init__(parent)
        self._model = QStringListModel(sorted(known_values or []))
        completer = QCompleter(self._model, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.setCompleter(completer)

    def set_known_values(self, values: list[str]) -> None:
        self._model.setStringList(sorted(set(values)))


class DetailEdit(QComboBox):
    """Detail field supporting two modes behind one line-edit-like API.

    Autocomplete mode: the dropdown lists values learned from the project.
    Fixed mode: the dropdown is a predefined list and learned values are
    ignored. Both stay editable so a custom value can always be typed.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._fixed_values: list[str] | None = None  # None = autocomplete mode
        self._learned_values: list[str] = []
        self._integer = False
        self._multi_menu = None
        # Parented deliberately - see the note in video_player about why
        # sole ownership by the setter is the wrong shape under PySide.
        completer = QCompleter(self.model(), self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.setCompleter(completer)

    # -- mode ----------------------------------------------------------

    def enable_multiple_values(self) -> None:
        self._multi_menu = QMenu(self)
        # Completing a whole combo value would discard the other selections.
        self.setCompleter(None)

    def toggle_value(self, value: str) -> None:
        selected = split_results(self.text())
        key = value.casefold()
        if any(item.casefold() == key for item in selected):
            selected = [item for item in selected if item.casefold() != key]
        else:
            selected.append(value)
        # Only reuse serialization, never the football outcome implication rules.
        self.setText(join_results(selected))

    def showPopup(self) -> None:
        if self._multi_menu is None:
            return super().showPopup()
        menu = self._multi_menu
        menu.clear()
        selected = split_results(self.text())
        options = split_results(join_results(
            [self.itemText(i) for i in range(self.count())] + selected))
        for value in options:
            item = menu.addAction(value)
            item.setCheckable(True)
            item.setChecked(value.casefold() in {v.casefold() for v in selected})
            item.triggered.connect(lambda _checked=False, v=value: self.toggle_value(v))
        menu.addSeparator()
        menu.addAction("Clear actions", self.clear)
        menu.popup(self.mapToGlobal(QPoint(0, self.height())))

    def keyPressEvent(self, event) -> None:
        if self._multi_menu is not None and event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.showPopup()
            event.accept()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:
        if self._multi_menu is not None:
            event.ignore()
            return
        super().wheelEvent(event)

    def set_integer_mode(self, enabled: bool) -> None:
        """Restrict the line to an integer yards value, or restore a list."""
        self._integer = bool(enabled)
        line = self.lineEdit()
        if line is None:
            return
        if self._integer:
            self._fixed_values = None
            self._repopulate()
            line.setValidator(QIntValidator(-99, 109, self))
            line.setPlaceholderText("yd")
        else:
            line.setValidator(None)
            if line.placeholderText() == "yd":
                line.setPlaceholderText("")

    def set_fixed_values(self, values: list[str] | None) -> None:
        """Fixed list, or None to return to learned autocomplete values."""
        if self._integer:
            return
        self._fixed_values = list(values) if values is not None else None
        self._repopulate()

    def set_known_values(self, values: list[str]) -> None:
        if self._integer:
            return
        self._learned_values = sorted(set(values))
        if self._fixed_values is None:
            self._repopulate()

    def _repopulate(self) -> None:
        current = self.currentText()
        self.blockSignals(True)
        super().clear()
        self.addItem("")
        self.addItems(self._fixed_values if self._fixed_values is not None
                      else self._learned_values)
        self.setEditText(current)
        self.blockSignals(False)

    # -- QLineEdit-compatible API --------------------------------------

    def text(self) -> str:
        return self.currentText()

    def setText(self, text: str) -> None:
        self.setEditText(text)

    def clear(self) -> None:  # clears the value, not the dropdown items
        self.clearEditText()

    def setToolTip(self, text: str) -> None:  # keep line edit tooltip in sync
        super().setToolTip(text)
        if self.lineEdit():
            self.lineEdit().setToolTip(text)


def configure_detail_edit(
        edit: DetailEdit, key: str, settings,
        *, roster: list[str] | None = None) -> None:
    """Apply vocab kind, fixed lists, or a roster to one detail editor."""
    from tapesift.services.football_vocab import KIND_INTEGER, field_kind

    if field_kind(key) == KIND_INTEGER:
        edit.set_integer_mode(True)
        return
    edit.set_integer_mode(False)
    if roster is not None:
        edit.set_fixed_values(roster or None)
        return
    fixed = bool(settings and getattr(settings, "use_fixed_dropdowns", False))
    options = (
        settings.fixed_details.get(key) if fixed and settings else None) or None
    edit.set_fixed_values(options if fixed and options else None)
