"""One attribute, one row: a fixed label then choices filling the rest.

The inspector had grown four idioms for the same job - a bordered result
grid, small chip rows, labelled full-width inputs, and dashed add-chips -
which is why it stopped working once the panel got narrow. This is the one
shape they collapse into: a label on the left at a fixed width, and equal
buttons dividing whatever is left.

Because every row divides the same span, the buttons line up in columns
down the panel, so reading what is set on a play is a single vertical
sweep rather than a hunt through four layouts.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QWidget)

LABEL_W = 74
ROW_H = 26
GAP = 4
#: The label's own line once the row stacks.
STACK_LABEL_H = 14
#: A cell narrower than this stops being a word and becomes a smear.
MIN_CELL_W = 44
MAX_CELL_W = 96
#: Distance is usually one of a few numbers, but "3rd & 7" has to be
#: typeable, and inside the five it is not a number at all.
GOAL_TOKENS = ("g", "goal")


class AttributeRowsPanel(QWidget):
    """Holds the rows and makes them agree on one shape.

    Left to themselves, a four-choice row stacks while a three-choice row
    beside it stays inline, so half the labels sit left and half sit on
    top. The panel is read by scanning a column down it, and that only
    works if every row is laid out the same way.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        policy = QSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        # Without this the row's heightForWidth stops here and never
        # reaches the layout that actually allocates the height.
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        #: activate() below re-enters this from the resize it triggers.
        #: Without a guard that recurses until the stack goes.
        self._syncing = False

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        layout = self.layout()
        if layout is None:
            return super().heightForWidth(width)
        return layout.totalHeightForWidth(width)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.sync_row_shape()

    def sync_row_shape(self) -> None:
        """One pass: pick the shape, then give every row its height.

        Heights are assigned here rather than negotiated through
        heightForWidth. Negotiation kept handing stacked rows a one-line
        allocation - cells clipped to blank slivers - and setting the
        height from inside each row's own resizeEvent left them
        overlapping, because the layout had already run. From here the
        panel sets both and the layout runs once, after.
        """
        if getattr(self, "_syncing", False):
            return
        rows = self.findChildren(AttributeRowWidget)
        if not rows:
            return
        width = max(1, self.width())
        self._syncing = True
        try:
            self._sync_rows(rows, width)
        finally:
            self._syncing = False

    def _sync_rows(self, rows: list["AttributeRowWidget"],
                   width: int) -> None:
        # The widest row decides for all of them.
        stacked = width < max(row.inline_needed() for row in rows)
        for row in rows:
            row.set_stack_override(stacked)
            wanted = row.heightForWidth(width)
            if row.height() != wanted or row.minimumHeight() != wanted:
                row.setFixedHeight(wanted)
        # The layout has not run yet, so every row is still holding cells
        # placed for its previous width. Settle it, then place them again
        # against the geometry they actually ended up with.
        layout = self.layout()
        if layout is not None:
            layout.activate()
        for row in rows:
            row._place()


class AttributeRowWidget(QWidget):
    """A labelled row of exclusive choices, optionally ending in a field."""

    valueChanged = Signal(str)

    def __init__(
            self, label: str, choices: tuple[str, ...], *,
            entry: bool = False, entry_mode: str = "distance",
            placeholder: str = "",
            parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("V2AttributeRow")
        policy = QSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

        self._choices = tuple(choices)
        self._entry_mode = entry_mode
        self._value = ""
        #: Set by the panel so every row stacks together. Rows deciding
        #: for themselves put some labels left and some on top, which
        #: breaks the column alignment the panel is read by.
        self._stack_override: bool | None = None

        # No QLayout. A layout cannot move the label onto its own line
        # and wrap the cells underneath, which is exactly what has to
        # happen when the inspector is dragged to its narrowest.
        self.label = QLabel(label.upper(), self)
        self.label.setProperty("role", "attributeRowLabel")

        self.buttons: dict[str, QPushButton] = {}
        self._stacked = False
        self._add_buttons()

        self.entry: QLineEdit | None = None
        if entry:
            # Same footprint as a preset, so the row still reads as one set
            # of equal cells rather than buttons plus an odd field.
            self.entry = QLineEdit(self)
            self.entry.setProperty("attributeEntry", "true")
            self.entry.setPlaceholderText(placeholder)
            self.entry.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.entry.setFixedHeight(ROW_H)
            self.entry.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.entry.editingFinished.connect(self._entry_committed)
            self.entry.setMinimumWidth(0)
            self.entry.show()

    def _add_buttons(self) -> None:
        """Cells are children this row positions itself."""
        for choice in self._choices:
            button = QPushButton(choice, self)
            button.setProperty("attributeChoice", "true")
            button.setCheckable(True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setMinimumWidth(0)
            button.clicked.connect(
                lambda _checked=False, value=choice: self.set_value(value))
            button.show()
            self.buttons[choice] = button

    def set_choices(self, choices: tuple[str, ...]) -> None:
        """Follow a vocabulary the user can edit, not a fixed list.

        Results are the one set of answers a project owns, so the rows
        showing them have to change when the project's favourites do.
        """
        wanted = tuple(choices)
        if wanted == self._choices:
            return
        for button in self.buttons.values():
            # deleteLater alone. Adding setParent(None) to this is the
            # double-free that took the whole suite down.
            button.hide()
            button.deleteLater()
        self.buttons = {}
        self._choices = wanted
        self._add_buttons()
        self._sync()

    # ------------------------------------------------------------- value

    def value(self) -> str:
        return self._value

    def set_value(self, value: str) -> None:
        """One value, however it was entered - preset or typed."""
        normalized = str(value).strip()
        changed = normalized != self._value
        self._value = normalized
        self._sync()
        if changed:
            self.valueChanged.emit(self._value)

    def _sync(self) -> None:
        """A preset and the field are two ways in, never two values."""
        matched = self._matching_choice(self._value)
        for choice, button in self.buttons.items():
            button.setChecked(choice == matched)
        if self.entry is None:
            return
        text = "" if matched else self._value
        if self.entry.text().strip() != text:
            self.entry.blockSignals(True)
            self.entry.setText(text)
            self.entry.blockSignals(False)

    def _matching_choice(self, value: str) -> str | None:
        folded = value.casefold()
        for choice in self._choices:
            if choice.casefold() == folded:
                return choice
        return None

    def _entry_committed(self) -> None:
        raw = self.entry.text().strip() if self.entry else ""
        self.set_value(self.normalize_entry(raw, self._entry_mode))

    @staticmethod
    def normalize_entry(raw: str, mode: str = "distance") -> str:
        """Accept a number, or G for goal-to-go.

        Inside the five the distance is not a distance, and every football
        surface writes it as Goal. Taking bare G keeps that one keystroke.

        Field position is not a distance at all - "Own 35" has to survive
        being typed - so a text row keeps what was written.
        """
        text = str(raw).strip()
        if not text:
            return ""
        if mode != "distance":
            return text
        if text.casefold() in GOAL_TOKENS:
            return "Goal"
        digits = "".join(ch for ch in text if ch.isdigit())
        return digits or ""

    # -------------------------------------------------------- layout

    def _cells(self) -> list[QWidget]:
        cells: list[QWidget] = list(self.buttons.values())
        if self.entry is not None:
            cells.append(self.entry)
        return cells

    def _cell_min(self) -> int:
        """Wide enough for the longest word in this row, within reason.

        Measured from the buttons only. A line edit asks for a comfortable
        typing width it does not need, and letting it vote here stacked
        every row that had a typed slot no matter how much space there was.
        """
        if not self.buttons:
            return MIN_CELL_W
        widest = max(b.sizeHint().width() for b in self.buttons.values())
        return max(MIN_CELL_W, min(MAX_CELL_W, widest))

    def _plan(self, width: int) -> tuple[bool, int, float]:
        """Decide (stacked, cells per line, cell width) for this width.

        Inline while the label and every cell fit on one line. Past that
        the label takes its own line and the cells wrap underneath, which
        is what keeps the panel readable instead of clipped when the dock
        is dragged in.
        """
        cells = self._cells()
        count = len(cells)
        if count == 0:
            return False, 0, 0.0
        stack = (self._stack_override if self._stack_override is not None
                 else width < self.inline_needed())
        if not stack:
            span = width - LABEL_W - GAP
            return False, count, (span - GAP * (count - 1)) / count
        per_line = max(1, int((width + GAP) // (self._cell_min() + GAP)))
        per_line = min(per_line, count)
        span = width - GAP * (per_line - 1)
        return True, per_line, span / per_line

    def inline_needed(self) -> int:
        """The width at which this row still fits on one line."""
        count = len(self._cells())
        if count == 0:
            return LABEL_W
        return LABEL_W + GAP * count + self._cell_min() * count

    def set_stack_override(self, stacked: bool | None) -> None:
        if stacked == self._stack_override:
            return
        self._stack_override = stacked
        self._place()
        # updateGeometry() on the row already invalidates the parent's
        # layout. Calling updateGeometry() on the parent as well re-enters
        # this from its resizeEvent and recurses until the stack goes.
        self.updateGeometry()

    def _line_count(self, width: int) -> int:
        stacked, per_line, _ = self._plan(width)
        if not stacked or per_line <= 0:
            return 1
        cells = len(self._cells())
        return (cells + per_line - 1) // per_line

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        stacked, _, _ = self._plan(width)
        if not stacked:
            return ROW_H
        lines = self._line_count(width)
        return STACK_LABEL_H + GAP + lines * ROW_H + GAP * (lines - 1)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place()

    def showEvent(self, event) -> None:
        # Qt withholds resizeEvent until a widget is first shown, so a row
        # built at one width and revealed at another would show the old
        # placement for a frame.
        super().showEvent(event)
        self._place()

    def _place(self) -> None:
        width = max(1, self.width())
        stacked, per_line, cell_w = self._plan(width)
        self._stacked = stacked
        cells = self._cells()
        if stacked:
            self.label.setGeometry(0, 0, width, STACK_LABEL_H)
            top = STACK_LABEL_H + GAP
            for index, cell in enumerate(cells):
                line, column = divmod(index, per_line)
                # The last line stretches its cells so the row stays a
                # block rather than trailing off mid-width.
                remaining = len(cells) - line * per_line
                span = min(per_line, remaining)
                this_w = (width - GAP * (span - 1)) / span
                cell.setGeometry(
                    int(column * (this_w + GAP)),
                    int(top + line * (ROW_H + GAP)),
                    int(this_w), ROW_H)
        else:
            self.label.setGeometry(0, 0, LABEL_W, ROW_H)
            for index, cell in enumerate(cells):
                cell.setGeometry(
                    int(LABEL_W + GAP + index * (cell_w + GAP)), 0,
                    int(cell_w), ROW_H)
        self.updateGeometry()

    def is_stacked(self) -> bool:
        """True when the row gave the label its own line."""
        return self._stacked

    def sizeHint(self) -> QSize:
        # Deliberately independent of the current width. A hint that read
        # self.width() is circular - the layout asks for the hint in order
        # to decide the width - and it settled on a stale answer, handing
        # stacked rows a one-line height that clipped their cells to
        # blank slivers. heightForWidth is what carries the real height.
        return QSize(self.inline_needed(), ROW_H)

    def minimumSizeHint(self) -> QSize:
        # One cell wide. Anything larger stops the dock from shrinking
        # and the panel gets clipped instead of restacking.
        return QSize(MIN_CELL_W, ROW_H)
