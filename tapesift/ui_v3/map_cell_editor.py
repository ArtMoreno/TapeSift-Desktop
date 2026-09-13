"""A local draft for one map cell; Clip Details still owns validation and saving."""
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMenu, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from tapesift.services import result_service
from tapesift.services.football_vocab import values_for
from tapesift.ui_v2.attribute_grid import EDIT_CHOICES, normalize_down_distance
from tapesift.ui_v3.result_picker import DEFAULT_SHORTCUTS, CAPTIONS


class MapCellEditor(QFrame):
    def __init__(self, window, clip, row, anchor):
        super().__init__(window, Qt.WindowType.Popup)
        self.setObjectName("MapCellEditor")
        self.setAccessibleName(f"Edit {row.label}")
        self.owner, self.clip_id, self.row_key = window, clip.id, row.key
        window.player.attribute_grid.setProperty("mapEditorOpen", True)
        self.values = {}
        self.details = dict(clip.details)
        self.fields = {}
        self.setStyleSheet(
            "QWidget {font:12px 'IBM Plex Sans';}"
            "QFrame#MapCellEditor {background:#151b20; border:1px solid #52606b;}"
            "QLabel {background:transparent; color:#dde4e9; border:0;}"
            "QPushButton {background:#202a32; color:#e4e9ec; border:1px solid #45525c;"
            "padding:5px 8px; border-radius:3px;}"
            "QPushButton:checked {background:#234658; border-color:#83afa9;}"
            "QPushButton:focus {border-color:#9dd9be;}"
            "QLineEdit, QPlainTextEdit {background:#0c1115; color:#e4e9ec;"
            "border:1px solid #45525c; padding:5px;}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 8, 9, 8)
        layout.setSpacing(6)
        layout.addWidget(QLabel(row.label))
        choices = QGridLayout()
        choices.setSpacing(4)
        layout.addLayout(choices)
        # Run/Pass classification and an RPO concept can both be present.
        # Checks are derived from the draft, including values typed by hand.
        self.buttons = {}
        self.choice_values = {}

        def field(key, caption, value):
            if caption:
                layout.addWidget(QLabel(caption))
            edit = QLineEdit(value)
            edit.setAccessibleName(caption or row.label)
            self.fields[key] = edit
            layout.addWidget(edit)
            return edit

        options = EDIT_CHOICES.get(row.key, ())
        if row.key == "primary_tag":
            options = (("Run", {"run_pass": "Run"}), ("Pass", {"run_pass": "Pass"}),
                       ("RPO", {"play_type": "RPO"}))
        elif row.key in {"result", "action"}:
            field(row.key, "Separate multiple values with ;", clip.details.get(row.key, ""))
            favorites = window.clip_editor._v3_shortcuts
            common = (DEFAULT_SHORTCUTS if favorites is None else favorites)[:12] if row.key == "result" else window.clip_editor._action_values()
            options = tuple((v, {row.key: v}) for v in common)
        elif row.key == "down":
            edit = field("down_distance", "Down & distance · e.g. 2nd & 8 or 3rd & Goal",
                         clip.details.get("down_distance", ""))
        elif row.key == "people":
            field("player_name", "Primary player", clip.details.get("player_name", ""))
            field("other_players", "Other players · separate with ;", clip.details.get("other_players", ""))
        elif row.key == "notes":
            self.notes = QPlainTextEdit(clip.notes)
            self.notes.setAccessibleName("Notes")
            self.notes.setFixedSize(300, 84)
            layout.addWidget(self.notes)

        for i, (caption, values) in enumerate(options):
            button = QPushButton(CAPTIONS.get(caption, caption) if row.key == "result" else caption)
            button.setCheckable(True)
            self.buttons[caption] = button
            self.choice_values[caption] = values
            choices.addWidget(button, i // 4, i % 4)
            button.clicked.connect(lambda _checked=False, v=values: self.pick(v))
        if row.key in {"primary_tag", "result", "action"}:
            more = QPushButton("More…")
            menu = QMenu(more)
            field_key = "play_type" if row.key == "primary_tag" else row.key
            for value in values_for(field_key):
                menu.addAction(value, lambda v=value: self.pick({field_key: v}))
            if row.key == "primary_tag":
                menu.addAction("Clear play type", lambda: self.pick({"run_pass": "", "play_type": "", "play_action": ""}))
            more.setMenu(menu)
            choices.addWidget(more, len(options) // 4, len(options) % 4)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setMaximumWidth(360)
        self.error.hide()
        layout.addWidget(self.error)
        bottom = QHBoxLayout()
        self.hint = QLabel("Enter apply · Esc cancel")
        bottom.addWidget(self.hint)
        apply = QPushButton("Apply")
        apply.clicked.connect(lambda: self.apply())
        bottom.addWidget(apply)
        layout.addLayout(bottom)
        for edit in self.fields.values():
            edit.textChanged.connect(self.sync_choices)
        self.sync_choices()
        for child in self.findChildren(QFrame) + self.findChildren(QLineEdit) + self.findChildren(QPushButton):
            child.installEventFilter(self)
        self.installEventFilter(self)
        self.adjustSize()
        bounds = window.screen().availableGeometry()
        x = max(bounds.left(), min(anchor.left(), bounds.right() - self.width() + 1))
        y = anchor.top() - self.height() - 4
        if y < bounds.top():
            y = min(anchor.bottom() + 4, bounds.bottom() - self.height() + 1)
        self.move(QPoint(x, y))
        self.show()
        focus = next(iter(self.fields.values()), None) or self.buttons.get(row.value(clip))
        focus = focus or (self.notes if row.key == "notes" else next(iter(self.buttons.values()), apply))
        focus.setFocus()
        if isinstance(focus, QLineEdit):
            focus.selectAll()
        window._sync_map_keyboard_hint()

    def pick(self, values):
        from tapesift.ui_v2.attribute_grid import set_down_keeping_distance
        if self.row_key in {"result", "action"}:
            edit = self.fields[self.row_key]
            edit.setText(result_service.toggle_result(edit.text(), values[self.row_key]))
        elif self.row_key == "down":
            edit = self.fields["down_distance"]
            edit.setText(set_down_keeping_distance(edit.text(), values["down_distance"]))
        else:
            if values.get("play_type") in {"Screen", "Scramble"}:
                values = {**values, "run_pass": "Pass" if values["play_type"] == "Screen" else "Run"}
            elif values.get("run_pass") in {"Run", "Pass"} and self.values.get("play_type", self.details.get("play_type", "")).casefold() == "scramble":
                values = {**values, "play_type": ""}
            self.values.update(values)
            chosen = " · ".join(v for v in values.values() if v) or "Cleared"
            self.hint.setText(f"{chosen} · Enter apply · Esc cancel")
        self.sync_choices()

    def sync_choices(self):
        from tapesift.services.football_context import parse_down_distance
        current = {**self.details, **self.values, **{k: e.text() for k, e in self.fields.items()}}
        for caption, button in self.buttons.items():
            values = self.choice_values[caption]
            checked = all(
                result_service.has_result(current.get(k, ""), v)
                if self.row_key in {"result", "action"}
                else parse_down_distance(current.get(k, ""))[0] == parse_down_distance(v)[0]
                if self.row_key == "down" else current.get(k, "") == v
                for k, v in values.items())
            button.setChecked(checked)

    def eventFilter(self, watched, event):
        if event.type() in {QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress}:
            key = event.key()
            steps = {Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1,
                     Qt.Key.Key_Up: -4, Qt.Key.Key_Down: 4}
            buttons = list(self.buttons.values())
            if watched in buttons and key in steps:
                event.accept()
                if event.type() == QEvent.Type.KeyPress:
                    index = max(0, min(len(buttons) - 1, buttons.index(watched) + steps[key]))
                    buttons[index].setFocus()
                    if self.row_key not in {"result", "action"}:
                        buttons[index].click()
                return True
            typing = isinstance(watched, (QLineEdit, QPlainTextEdit))
            handled = key in {Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape}
            handled |= not typing and key in {Qt.Key.Key_W, Qt.Key.Key_B}
            if isinstance(watched, QPlainTextEdit) and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                handled = False
            if handled:
                event.accept()
                if event.type() == QEvent.Type.KeyPress:
                    if key == Qt.Key.Key_Escape:
                        self.close()
                    else:
                        self.apply(1 if key == Qt.Key.Key_W else -1 if key == Qt.Key.Key_B else 0)
                return True
        return super().eventFilter(watched, event)

    def apply(self, delta=0):
        values = {**self.values, **{k: edit.text() for k, edit in self.fields.items()}}
        if "down_distance" in values:
            raw = values["down_distance"].strip()
            normalized = normalize_down_distance(raw)
            if raw and not normalized:
                self.error.setText("Use a down and distance, such as 2nd & 8 or 3rd & Goal.")
                self.error.show()
                return
            values["down_distance"] = normalized
        notes = self.notes.toPlainText() if self.row_key == "notes" else None
        if not self.owner._apply_map_cell(self.clip_id, values, notes):
            self.error.setText(self.owner.clip_editor.error_label.text() or "Could not save this play.")
            self.error.show()
            return
        self.close()
        if delta:
            self.owner._keyboard_clip_step(delta)

    def hideEvent(self, event):
        super().hideEvent(event)
        self.owner.player.attribute_grid.setProperty("mapEditorOpen", False)
        self.owner._focus_map_row(self.row_key)
