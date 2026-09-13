"""Compact result selection and shortcut editing for the V3 inspector."""
from __future__ import annotations

from PySide6.QtCore import QMimeData, QPoint, Qt, Signal
from PySide6.QtGui import QDrag, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from tapesift.services import result_service
from tapesift.services.football_vocab import lookup
from tapesift.ui.result_manager_dialog import unique_results

DEFAULT_SHORTCUTS = (
    "Gain", "No Gain", "Loss", "TFL", "Reception", "Completion", "Incompletion", "Drop",
    "First Down", "Touchdown", "Sack", "Interception", "Fumble", "Fumble Lost", "Penalty", "YAC",
)
CAPTIONS = {"First Down": "1st Down", "Touchdown": "TD", "Interception": "INT",
            "Incompletion": "Incomplete"}
GROUPS = ("Yardage", "Passing", "Scoring & chains", "Defense & turnovers",
          "Penalties", "Special teams", "Other / custom")
PALETTES = {
    "neutral": ("#111214", "#262a2f", "#e6e8e6"),
    "sand": ("#141611", "#68634e", "#c7b58b"),
    "cool": ("#161818", "#526260", "#aebfc1"),
}


def result_group(value: str) -> str:
    if value in ("Gain", "No Gain", "Loss", "Explosive"):
        return GROUPS[0]
    if value in ("Completion", "Reception", "Incompletion", "Incomplete", "Drop",
                 "Throwaway", "Batted Pass", "YAC", "Spike"):
        return GROUPS[1]
    item = lookup("result", value)
    if value == "Penalty" or (item and item.category == "Penalty"):
        return GROUPS[4]
    if value.startswith(("Field Goal", "Extra Point", "2-Point", "Punt", "Kickoff", "Onside")) or value == "Touchback":
        return GROUPS[5]
    if item and item.category in ("Score", "First Down"):
        return GROUPS[2]
    if value in ("Sack", "TFL", "Interception", "INT", "Fumble", "Fumble Lost",
                 "Fumble Recovered", "Turnover on Downs"):
        return GROUPS[3]
    return GROUPS[6]


def tint_button(button: QPushButton, palette: str) -> None:
    fill, border, text = PALETTES[palette]
    if palette == "neutral":
        hover_fill, hover_border = "#1c1f23", "#3a4046"
        disabled_text, disabled_border = "#8d949a", "#262a2f"
    else:
        hover_fill, hover_border = fill, text
        disabled_text, disabled_border = "#647269", "#303d34"
    button.setStyleSheet(f"""
        QPushButton {{ background:{fill}; color:{text}; border:1px solid {border};
            border-radius:3px; padding:3px 2px; font:11px 'IBM Plex Sans'; }}
        QPushButton:hover {{ background:{hover_fill}; border:1px solid {hover_border}; }}
        QPushButton:pressed {{ background:#0a0b0c; }}
        QPushButton:checked {{ background:#163a26; color:#c9f2d7; border:2px solid #1d7a45; }}
        QPushButton:focus {{ border:2px solid #ffc27b; }}
        QPushButton:disabled {{ color:{disabled_text}; border-color:{disabled_border}; }}
    """)


SURFACE_STYLE = """
    QDialog, QScrollArea, QWidget#ResultBody { background:#0a0b0c; color:#e6e8e6; }
    QLabel, QCheckBox { color:#e6e8e6; background:transparent; font:12px 'IBM Plex Sans'; }
    QLabel[groupHeading="true"] { color:#8d949a; font-size:11px; padding-top:8px; }
    QLineEdit { background:#0d0e10; color:#e6e8e6; border:1px solid #262a2f;
        border-radius:3px; padding:7px; font-size:13px; }
    QPushButton { background:#111214; color:#e6e8e6; border:1px solid #262a2f;
        border-radius:3px; padding:6px; font-size:12px; }
    QPushButton:hover { background:#1c1f23; border-color:#3a4046; }
    QPushButton:focus, QLineEdit:focus { border-color:#ffc27b; }
    QPushButton:checked { background:#163a26; color:#c9f2d7; border-color:#1d7a45; }
    QPushButton:pressed { background:#0a0b0c; }
    QPushButton:disabled { color:#8d949a; border-color:#262a2f; }
    QPushButton[primary="true"] { background:#39e07a; color:#062312; border-color:#39e07a; }
    QPushButton[primary="true"]:hover { background:#4ae88a; border-color:#4ae88a; }
    QPushButton[primary="true"]:focus { background:#4ae88a; border-color:#ffc27b; }
    QPushButton[primary="true"]:disabled { background:#1a3a28; color:#5f8a70; border-color:#1a3a28; }
    QCheckBox { spacing:7px; min-height:25px; }
    QCheckBox::indicator { width:15px; height:15px; border:1px solid #3a4046;
        border-radius:3px; background:#0d0e10; }
    QCheckBox::indicator:checked { background:#39e07a; border-color:#39e07a; }
    QCheckBox::indicator:disabled { background:#0d0e10; border-color:#262a2f; }
    QScrollArea { border:0; }
"""


class ResultsPicker(QDialog):
    """Bounded, searchable multi-select; edits use the inspector's existing toggle."""
    result_toggled = Signal(str)
    custom_added = Signal(str, bool)

    def __init__(self, values, current: str, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setWindowTitle("All results")
        self.setStyleSheet(SURFACE_STYLE)
        self.resize(370, 530)
        self._current = current
        self._values = unique_results(values, result_service.split_results(current))
        outer = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addWidget(QLabel("All results"), 1)
        close = QPushButton("×")
        close.setAccessibleName("Close results")
        close.clicked.connect(self.close)
        header.addWidget(close)
        outer.addLayout(header)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search results…")
        self.search.setAccessibleName("Search all results")
        self.search.setClearButtonEnabled(True)
        outer.addWidget(self.search)
        filters = QHBoxLayout()
        self.filter_buttons = {}
        for name in ("All", "Offense", "Defense"):
            button = QPushButton(name)
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, n=name: self._filter(n))
            filters.addWidget(button)
            self.filter_buttons[name] = button
        outer.addLayout(filters)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("ResultBody")
        self.body_layout = QVBoxLayout(body)
        self.body_layout.setContentsMargins(0, 0, 4, 0)
        self.scroll.setWidget(body)
        outer.addWidget(self.scroll, 1)
        self.checks = {}
        self.group_widgets = {}
        self._build_groups()
        self.search.textChanged.connect(lambda: self._filter())
        self._filter("All")
        self.custom_row = QWidget()
        custom = QVBoxLayout(self.custom_row)
        custom.setContentsMargins(0, 0, 0, 0)
        self.custom_name = QLineEdit()
        self.custom_name.setPlaceholderText("New result name")
        self.custom_name.setMaxLength(80)
        self.pin_custom = QCheckBox("Pin to quick buttons")
        add = QPushButton("Add result")
        add.clicked.connect(self._add_custom)
        self.custom_name.returnPressed.connect(self._add_custom)
        custom.addWidget(self.custom_name)
        custom.addWidget(self.pin_custom)
        custom.addWidget(add)
        outer.addWidget(self.custom_row)
        self.custom_row.hide()
        footer = QHBoxLayout()
        new = QPushButton("+ Add custom result")
        new.clicked.connect(self._show_custom)
        done = QPushButton("Done")
        done.setProperty("primary", "true")
        done.clicked.connect(self.close)
        footer.addWidget(new, 1)
        footer.addWidget(done)
        outer.addLayout(footer)

    def _build_groups(self):
        for group in GROUPS:
            section = QWidget()
            section.setObjectName("ResultBody")
            layout = QVBoxLayout(section)
            layout.setContentsMargins(0, 0, 0, 0)
            heading = QLabel(group.upper())
            heading.setProperty("groupHeading", "true")
            layout.addWidget(heading)
            grid = QGridLayout()
            slot = 0
            for value in (v for v in self._values if result_group(v) == group):
                check = QCheckBox(value)
                check.setMinimumWidth(0)
                check.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
                check.setToolTip(value)
                check.clicked.connect(lambda _=False, v=value: self.result_toggled.emit(v))
                # Long foul names get the full width instead of truncating.
                if len(value) > 20:
                    slot += slot % 2
                    grid.addWidget(check, slot // 2, 0, 1, 2)
                    slot += 2
                else:
                    grid.addWidget(check, slot // 2, slot % 2)
                    slot += 1
                self.checks[value] = check
            layout.addLayout(grid)
            self.body_layout.addWidget(section)
            self.group_widgets[group] = section
        self.body_layout.addStretch()
        self.sync(self._current)

    def sync(self, current: str):
        self._current = current
        for value, check in self.checks.items():
            selected = result_service.has_result(current, value)
            check.setChecked(selected)
            check.setText(("✓ " if selected else "") + value.replace("&", "&&"))
            check.setAccessibleName(value)

    def _filter(self, name=None):
        if name is not None:
            self._side = name
        query = self.search.text().strip().casefold()
        for name, button in self.filter_buttons.items():
            button.setChecked(name == self._side)
        for group, section in self.group_widgets.items():
            visible = False
            for value, check in self.checks.items():
                if result_group(value) != group:
                    continue
                side = self._side
                # These are browsing filters, never changes to saved result semantics.
                side_matches = side == "All" or group not in (
                    ("Defense & turnovers",) if side == "Offense" else ("Passing",))
                match = query in value.casefold() and (bool(query) or side_matches)
                check.setVisible(match)
                visible |= match
            section.setVisible(visible)

    def _show_custom(self):
        self.custom_row.show()
        self.custom_name.setFocus()

    def _add_custom(self):
        value = self.custom_name.text().strip()
        if not value or any(c in value for c in (";", "\n", "\r")):
            self.custom_name.setPlaceholderText("Enter one result name (no semicolons)")
            return
        self.custom_added.emit(value, self.pin_custom.isChecked())


class ShortcutTile(QFrame):
    """A drag handle and explicit remove button without changing saved tags."""
    moved = Signal(int, int)
    removed = Signal(int)
    MIME = "application/x-tapesift-result-shortcut"

    def __init__(self, index, value, parent=None):
        super().__init__(parent)
        self.index = index
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(f"Shortcut {index + 1}: {value}. Alt Left or Right to reorder.")
        for key, delta in (("Alt+Left", -1), ("Alt+Right", 1)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda d=delta: self.moved.emit(self.index, self.index + d))
        fill, border, color = PALETTES["neutral"]
        self.setStyleSheet(f"QFrame {{background:{fill}; border:1px solid {border}; border-radius:3px;}}"
                          f"QFrame:focus {{border:1px solid #ffc27b;}}"
                          f"QLabel {{border:0; color:{color}; font-size:11px;}}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 3, 5, 5)
        header = QHBoxLayout()
        handle = QLabel(f"{index + 1}  ⠿")
        handle.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        header.addWidget(handle, 1)
        remove = QPushButton("×")
        remove.setFixedSize(22, 22)
        remove.setAccessibleName("Remove shortcut " + value)
        remove.clicked.connect(lambda: self.removed.emit(self.index))
        header.addWidget(remove)
        layout.addLayout(header)
        caption = QLabel(CAPTIONS.get(value, value))
        caption.setWordWrap(True)
        caption.setMinimumWidth(0)
        caption.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        caption.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(caption, 1)
        self.setToolTip(value + " · drag to reorder")
        self.setMinimumHeight(65)
        self._press = QPoint()

    def mousePressEvent(self, event):
        self._press = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and (event.position().toPoint() - self._press).manhattanLength() > 8:
            drag = QDrag(self)
            mime = QMimeData()
            mime.setData(self.MIME, str(self.index).encode())
            drag.setMimeData(mime)
            drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, event):
        if isinstance(event.source(), ShortcutTile) and event.source().parent() is self.parent():
            event.acceptProposedAction()

    def dropEvent(self, event):
        source = event.source()
        if isinstance(source, ShortcutTile) and source.parent() is self.parent():
            self.moved.emit(source.index, self.index)
            event.acceptProposedAction()


class ResultShortcutsDialog(QDialog):
    def __init__(self, favorites, values, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit quick buttons")
        self.setStyleSheet(SURFACE_STYLE)
        self.resize(430, 620)
        self._favorites = unique_results(favorites)[:16]
        self._values = unique_results(values, favorites)
        outer = QVBoxLayout(self)
        outer.addWidget(QLabel("Edit quick buttons"))
        self.count = QLabel()
        outer.addWidget(self.count)
        self.tiles = QWidget()
        self.grid = QGridLayout(self.tiles)
        self.grid.setContentsMargins(0, 0, 0, 0)
        for column in range(4):
            self.grid.setColumnStretch(column, 1)
        outer.addWidget(self.tiles)
        hint = QLabel("Drag or Alt+← / → to reorder · × removes shortcut only.\nSaved clip results stay unchanged.")
        hint.setWordWrap(True)
        outer.addWidget(hint)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a result to add…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        outer.addWidget(self.search)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("ResultBody")
        self.available = QVBoxLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        self.add_buttons = {}
        self._rebuild_available()
        row = QHBoxLayout()
        self.custom_edit = QLineEdit()
        self.custom_edit.setPlaceholderText("New result name")
        self.custom_edit.setMaxLength(80)
        row.addWidget(self.custom_edit, 1)
        new = QPushButton("+ New result")
        new.clicked.connect(self.add_custom)
        self.custom_edit.returnPressed.connect(self.add_custom)
        row.addWidget(new)
        outer.addLayout(row)
        self.message = QLabel("")
        self.message.setWordWrap(True)
        outer.addWidget(self.message)
        reset = QPushButton("Reset defaults")
        reset.clicked.connect(self.reset)
        outer.addWidget(reset)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("Save layout")
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setProperty("primary", "true")
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)
        self._render()

    def favorites(self):
        return list(self._favorites)

    def results(self):
        return list(self._values)

    def _render(self):
        while self.grid.count():
            tile = self.grid.takeAt(0).widget()
            tile.hide()
            tile.deleteLater()
        for index, value in enumerate(self._favorites):
            tile = ShortcutTile(index, value, self.tiles)
            tile.moved.connect(self.move_shortcut)
            tile.removed.connect(self.remove)
            self.grid.addWidget(tile, index // 4, index % 4)
        self.count.setText(f"Choose and arrange shortcuts · {len(self._favorites)} / 16")
        self._filter()

    def move_shortcut(self, source, target):
        if 0 <= source < len(self._favorites) and 0 <= target < len(self._favorites):
            self._favorites.insert(target, self._favorites.pop(source))
            self._render()

    def remove(self, index):
        if 0 <= index < len(self._favorites):
            self._favorites.pop(index)
            self._render()

    def add(self, value):
        if value not in self._favorites and len(self._favorites) < 16:
            self._favorites.append(value)
            self._render()

    def reset(self):
        self._favorites = list(DEFAULT_SHORTCUTS)
        self._render()

    def _rebuild_available(self):
        for value in self._values:
            if value in self.add_buttons:
                continue
            button = QPushButton(value + "   +")
            button.clicked.connect(lambda _=False, v=value: self.add(v))
            self.available.addWidget(button)
            self.add_buttons[value] = button

    def _filter(self):
        query = self.search.text().casefold()
        selected = {v.casefold() for v in self._favorites}
        for value, button in self.add_buttons.items():
            button.setVisible(query in value.casefold() and value.casefold() not in selected)
            button.setEnabled(len(self._favorites) < 16)

    def add_custom(self):
        value = self.custom_edit.text().strip()
        if not value or any(c in value for c in (";", "\n", "\r")):
            self.message.setText("Enter one result name without semicolons.")
            return
        self._values = unique_results(self._values, [value])
        value = next(v for v in self._values if v.casefold() == value.casefold())
        self._rebuild_available()
        self.add(value)
        self.custom_edit.clear()
        self.message.setText("Added to the library. Remove a shortcut to pin it." if value not in self._favorites else "Added to shortcuts. Save layout to keep it.")
        self._filter()
