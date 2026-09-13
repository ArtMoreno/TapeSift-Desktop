"""A one-key reference for the keys that drive logging.

The transport is fast because it is keyboard-driven, but nothing on screen
said so - the status bar advertised four bindings out of thirty. This is the
sheet, opened with ? and closed with ? or Escape.

SHORTCUT_GROUPS is the single copy of that vocabulary. It is asserted against
the live shortcut table in the tests, so a binding cannot be renamed here and
left wrong in the app, or added to the app and quietly missing from here.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget)

#: (group title, ((display keys, what it does, bound sequence or None), ...))
#: The third item is the sequence the app actually registers. None marks a
#: binding the widget owns rather than the shortcut table - a wheel gesture,
#: or a key handled by a focused widget - so tests know not to look for it.
SHORTCUT_GROUPS: tuple[tuple[str, tuple[tuple[str, str, str | None], ...]], ...] = (
    ("TRANSPORT", (
        ("Space", "Play / pause", "Space"),
        ("J  K  L", "Shuttle back / stop / forward", "J"),
        ("Left  Right", "Frame step, or jump while playing", "Left"),
        ("Shift + Left/Right", "Jump back / forward", "Shift+Left"),
        ("Ctrl + Left/Right", "Seek five seconds", "Ctrl+Left"),
        ("Wheel", "Jog frames over the dial or the film", None),
    )),
    ("PANELS", (
        ("Ctrl + B", "Fold Play Details away, or bring it back", "Ctrl+B"),
        ("U", "Next play that still needs logging", "U"),
        ("Shift + U", "Previous play that still needs logging", "Shift+U"),
    )),
    ("PLAY GRID", (
        ("Arrows", "Move the cell cursor; left and right change play",
         None),
        ("1 - 9", "Set that cell straight from the row's choices", None),
        ("Enter", "Open the full picker, or type a down and distance",
         None),
        ("Esc", "Put the keyboard down", None),
    )),
    ("LOGGING", (
        ("I    O", "Mark in / out", "I"),
        ("A", "Add clip from the marks", "A"),
        ("C", "Cut the clip at the playhead", "C"),
        ("M", "Merge the selected plays", "M"),
        ("1 - 9", "Apply the quick tag in that slot", None),
        ("[    ]", "Nudge in / out", "["),
        ("S", "Toggle timeline snapping", "S"),
        ("G", "Find or jump to the predicted snap", "G"),
    )),
    ("RUN / PASS", (
        ("R", "Tag this play a run", "R"),
        ("P", "Tag this play a pass", "P"),
        ("1 - 9", "Play action, RPO, screen and the rest", None),
    )),
    ("REVIEW", (
        ("B / W", "Previous / next clip; saves edits (V3)", None),
        ("E", "Focus Clip Details (V3)", None),
        ("Tab / Shift+Tab", "Next / previous field; Space clicks", None),
        ("Ctrl+Shift+Enter", "Save and next, including notes (V3)", None),
        ("F5", "Review mode", None),
        ("Enter", "Save and go to the next play", None),
        ("Ctrl + Enter", "Save", None),
        ("Esc", "Return to playback", "Esc"),
        ("Ctrl + Up/Down", "Previous / next play", "Ctrl+Up"),
        ("Shift + R", "Replay the current play", "Shift+R"),
        ("Ctrl + E", "Quick export", "Ctrl+E"),
        ("Ctrl + Z", "Undo", None),
    )),
)


class ShortcutsOverlay(QFrame):
    """Modal-looking sheet listing the keyboard vocabulary."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ShortcutsOverlay")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAccessibleName("Keyboard shortcuts")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Three columns of keycaps and prose measure far wider than they need
        # to read; without a cap the sheet spans a whole 4K window.
        self.setMaximumWidth(940)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(26, 20, 26, 22)
        outer.setSpacing(16)

        header = QHBoxLayout()
        title = QLabel("KEYBOARD")
        title.setObjectName("ShortcutsOverlayTitle")
        header.addWidget(title)
        header.addStretch(1)
        dismiss = QLabel("? or Esc to close")
        dismiss.setProperty("role", "subtle")
        header.addWidget(dismiss)
        outer.addLayout(header)

        columns = QHBoxLayout()
        columns.setSpacing(28)
        for group_title, rows in SHORTCUT_GROUPS:
            column = QVBoxLayout()
            column.setSpacing(7)
            caption = QLabel(group_title)
            caption.setProperty("role", "shortcutGroup")
            column.addWidget(caption)

            grid = QGridLayout()
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(6)
            for row, (keys, meaning, _binding) in enumerate(rows):
                cap = QLabel(keys)
                cap.setProperty("role", "shortcutKey")
                cap.setAlignment(
                    Qt.AlignmentFlag.AlignCenter)
                grid.addWidget(cap, row, 0)
                text = QLabel(meaning)
                text.setProperty("role", "shortcutMeaning")
                grid.addWidget(text, row, 1)
            grid.setColumnStretch(1, 1)
            column.addLayout(grid)
            column.addStretch(1)
            columns.addLayout(column, 1)
        outer.addLayout(columns)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Question):
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)
