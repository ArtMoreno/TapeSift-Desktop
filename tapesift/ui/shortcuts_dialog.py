"""Searchable, grouped reference for TapeSift keyboard shortcuts."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)


@dataclass(frozen=True)
class ShortcutItem:
    keys: str
    action: str
    note: str = ""


SHORTCUT_SECTIONS: tuple[tuple[str, str, tuple[ShortcutItem, ...]], ...] = (
    ("TRANSPORT", "Playback and navigation", (
        ShortcutItem("Space", "Play or pause"),
        ShortcutItem("J  /  K  /  L", "Shuttle reverse, stop, or forward", "Tap J or L again to increase speed: 1x, 2x, 4x, then 8x."),
        ShortcutItem("Left / Right", "Move one frame while paused", "Hold for continuous movement; while playing, these keys jump instead."),
        ShortcutItem("Shift + Left / Right", "Jump backward or forward", "Uses your configured jump duration."),
        ShortcutItem("Ctrl + Left / Right", "Jump backward or forward 5 seconds"),
        ShortcutItem(
            "G", "Find or jump to the predicted snap",
            "The first use analyzes the selected play; later uses jump to "
            "the saved timeline bookmark."),
    )),
    ("TIMELINE", "Zoom and fit the timeline", (
        ShortcutItem("Shift + Up", "Zoom timeline in (V3)", "Shows less time around the playhead. Hold to repeat through the zoom levels."),
        ShortcutItem("Shift + Down", "Zoom timeline out (V3)", "Shows more time around the playhead. Hold to repeat; stops at the full timeline."),
        ShortcutItem("Shift + F", "Fit Play (V3)", "Select a clip in the ledger or tag map first. Sets timeline zoom to fit that play with surrounding context."),
        ShortcutItem("Ctrl + 0", "Show the full timeline", "Resets timeline zoom to the whole video."),
    )),
    ("TAG MAP", "Edit visible rows from the keyboard (V3)", (
        ShortcutItem("Shift + E", "Start editing the selected clip on the tag map", "Select a clip first. Hidden rows are skipped; choose visible rows in Tools > Tag Map options."),
        ShortcutItem("J / K", "Move down / up through visible rows", "Works while the tag map has focus. Esc returns to playback, where J/K/L shuttle the video."),
        ShortcutItem("Enter", "Edit the focused tag cell", "Use the picker arrows or type in the field. Enter applies; Esc cancels the picker."),
        ShortcutItem("B / W", "Save and move to the previous / next clip", "Outside text fields. Pending valid edits are applied to Clip Details and saved before moving."),
    )),
    ("MARK & EDIT", "Create and repair clips", (
        ShortcutItem("I", "Mark the start of a play", "Sets the in-point. With At the snap prompts, also opens the details form."),
        ShortcutItem("O", "Mark the end of the play", "With default prompts, opens the details form. With prompts off: I, O, then A opens Name This Clip."),
        ShortcutItem("A", "Name or log the marked clip", "Uses the current in/out marks. Opens the details form when prompts are enabled, or Name This Clip when prompts are off."),
        ShortcutItem("C", "Cut the clip under the playhead", "Selects the only clip crossed by the white playhead and splits it there."),
        ShortcutItem("Ctrl + K", "Split the selected clip at the playhead", "Useful for fixing an over-long clip."),
        ShortcutItem("M", "Merge selected clips"),
        ShortcutItem("Ctrl + M", "Apply the previous clip's details", "Copies its label, tags, and play details."),
        ShortcutItem("Ctrl + Shift + V", "Jump to a copied timestamp"),
        ShortcutItem("Delete", "Remove the selected clip or clips"),
        ShortcutItem("Ctrl + D", "Duplicate the selected clip", "Creates a new version with the same range and separate metadata."),
    )),
    ("REVIEW MODE", "Log clips quickly, one after another", (
        ShortcutItem("B / W", "Previous / next clip (V3)", "Outside text fields. Saves pending details before moving; invalid edits keep you on the current clip."),
        ShortcutItem("E", "Focus Clip Details (V3)", "Tab / Shift+Tab moves through fields. Space activates the focused button. Esc returns to playback."),
        ShortcutItem("Ctrl + Shift + Enter", "Save and go to the next clip (V3)", "Also works while typing notes."),
        ShortcutItem("Ctrl + B / Ctrl + I", "Open or close Clip Details (V3)"),
        ShortcutItem("Ctrl + Shift + B", "Open or close the Clip Ledger (V3)"),
        ShortcutItem("F5  /  Ctrl + Shift + R",
                     "Enter or leave Review mode (also Playback menu)"),
        ShortcutItem(
            "Ctrl + Shift + D",
            "Open Detection Coverage review",
            "Check uncertain clips and possible-missed source ranges."),
        ShortcutItem("Ctrl + Down / Up", "Go to the next or previous clip", "Works even while typing in the inspector."),
        ShortcutItem("Page Down  /  Page Up", "Go to the next or previous clip"),
        ShortcutItem("Ctrl + Shift + Down", "Go to the next clip with no details"),
        ShortcutItem("Enter", "Save and advance", "Use while focus is in the inspector."),
        ShortcutItem("Ctrl + Enter", "Save and stay on the current clip"),
        ShortcutItem("R / P", "Tag the current clip Run / Pass"),
        ShortcutItem("Shift + R", "Replay the current clip"),
        ShortcutItem("Ctrl + L", "Turn looping on or off"),
        ShortcutItem("N", "Exclude the clip from export and move on"),
        ShortcutItem("[  /  ]", "Set the clip's in or out point to the playhead"),
    )),
    ("PROJECT", "Save, export, and undo", (
        ShortcutItem("Ctrl + S", "Save the project"),
        ShortcutItem(
            "Ctrl + E", "Quick Export the selected clip",
            "Uses the clip's assigned preset and the project's accurate-cut setting."),
        ShortcutItem("Ctrl + Z  /  Ctrl + Y", "Undo or redo"),
    )),
)


class ShortcutRow(QFrame):
    """One keyboard shortcut and its explanation."""

    def __init__(self, item: ShortcutItem, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.item = item
        self.setProperty("shortcutRow", True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(18)

        key = QLabel(item.keys)
        key.setProperty("keycap", True)
        key.setAlignment(Qt.AlignmentFlag.AlignCenter)
        key.setFixedWidth(168)
        layout.addWidget(key, 0, Qt.AlignmentFlag.AlignTop)

        copy = QVBoxLayout()
        copy.setSpacing(3)
        action = QLabel(item.action)
        action.setProperty("role", "shortcutAction")
        action.setWordWrap(True)
        copy.addWidget(action)
        if item.note:
            note = QLabel(item.note)
            note.setProperty("role", "subtle")
            note.setWordWrap(True)
            copy.addWidget(note)
        layout.addLayout(copy, 1)

    def matches(self, query: str) -> bool:
        text = f"{self.item.keys} {self.item.action} {self.item.note}".casefold()
        return query.casefold() in text


class ShortcutSection(QFrame):
    def __init__(self, eyebrow: str, title: str, items: tuple[ShortcutItem, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("shortcutSection", True)
        self.rows: list[ShortcutRow] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        header.setProperty("shortcutHeader", True)
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(15, 12, 15, 11)
        header_layout.setSpacing(2)
        eyebrow_label = QLabel(eyebrow)
        eyebrow_label.setProperty("role", "eyebrow")
        header_layout.addWidget(eyebrow_label)
        title_label = QLabel(title)
        title_label.setProperty("role", "sectionTitle")
        header_layout.addWidget(title_label)
        layout.addWidget(header)

        for item in items:
            row = ShortcutRow(item)
            self.rows.append(row)
            layout.addWidget(row)

    def apply_filter(self, query: str) -> int:
        visible = 0
        for row in self.rows:
            matches = row.matches(query)
            row.setVisible(matches)
            visible += int(matches)
        self.setVisible(visible > 0)
        return visible


class ShortcutsDialog(QDialog):
    """A compact shortcut reference designed for repeated in-app use."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ShortcutsDialog")
        self.setWindowTitle("Keyboard Shortcuts")
        self.setModal(True)
        self.resize(760, 680)
        self.setMinimumSize(620, 480)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)

        heading_row = QHBoxLayout()
        heading_copy = QVBoxLayout()
        heading_copy.setSpacing(3)
        eyebrow = QLabel("TAPESIFT  /  QUICK REFERENCE")
        eyebrow.setProperty("role", "eyebrow")
        heading_copy.addWidget(eyebrow)
        title = QLabel("Keyboard shortcuts")
        title.setProperty("role", "dialogTitle")
        heading_copy.addWidget(title)
        subtitle = QLabel(
            "V3: ? or F1 opens this guide; Esc closes it. Timeline zoom works from playback "
            "or the tag map, without moving the playhead or changing clip boundaries. "
            "Text fields, pickers and dialogs keep their editing keys; Esc returns to playback.")
        subtitle.setWordWrap(True)
        subtitle.setProperty("role", "subtle")
        heading_copy.addWidget(subtitle)
        heading_row.addLayout(heading_copy, 1)

        total = sum(len(items) for _, _, items in SHORTCUT_SECTIONS)
        self.result_count = QLabel(f"{total} SHORTCUTS")
        self.result_count.setProperty("badge", True)
        heading_row.addWidget(self.result_count, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(heading_row)

        self.search = QLineEdit()
        self.search.setObjectName("ShortcutSearch")
        self.search.setPlaceholderText("Search shortcuts - try ‘zoom’, ‘Fit Play’, ‘tag map’, or ‘replay’")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        root.addWidget(self.search)

        scroll = QScrollArea()
        scroll.setObjectName("ShortcutScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        container = QWidget()
        container.setObjectName("ShortcutContainer")
        sections_layout = QVBoxLayout(container)
        sections_layout.setContentsMargins(0, 0, 4, 0)
        sections_layout.setSpacing(12)

        self.sections: list[ShortcutSection] = []
        for eyebrow_text, section_title, items in SHORTCUT_SECTIONS:
            section = ShortcutSection(eyebrow_text, section_title, items)
            self.sections.append(section)
            sections_layout.addWidget(section)
        sections_layout.addStretch(1)
        scroll.setWidget(container)
        root.addWidget(scroll, 1)

        footer = QHBoxLayout()
        hint = QLabel("Press F1 anytime to open this reference.")
        hint.setProperty("role", "subtle")
        footer.addWidget(hint, 1)
        close_button = QPushButton("Done")
        close_button.setProperty("accent", True)
        close_button.setDefault(True)
        close_button.clicked.connect(self.accept)
        footer.addWidget(close_button)
        root.addLayout(footer)

        self.setStyleSheet(
            """
            QDialog#ShortcutsDialog { background: #0a0c0b; }
            #ShortcutsDialog QLabel[role="dialogTitle"] { color: #f2f5f2; font-size: 26px; font-weight: 800; }
            #ShortcutsDialog QLabel[role="sectionTitle"] { color: #f2f5f2; font-size: 15px; font-weight: 700; }
            #ShortcutsDialog QLabel[role="shortcutAction"] { color: #edf2ee; font-size: 13px; font-weight: 650; }
            #ShortcutsDialog QLabel[keycap="true"] {
                color: #dff9e7; background: #172019; border: 1px solid #3f5846;
                border-bottom: 2px solid #526b59; border-radius: 6px; padding: 7px 9px;
                font-family: "Cascadia Mono", Consolas; font-size: 12px; font-weight: 700;
            }
            #ShortcutsDialog QFrame[shortcutSection="true"] { background: #111512; border: 1px solid #273029; border-radius: 9px; }
            #ShortcutsDialog QWidget[shortcutHeader="true"] { background: #0e120f; border-bottom: 1px solid #273029; }
            #ShortcutsDialog QFrame[shortcutRow="true"] { background: transparent; border: none; border-bottom: 1px solid #202721; }
            #ShortcutsDialog QFrame[shortcutRow="true"]:hover { background: #151b17; }
            #ShortcutsDialog QScrollArea, #ShortcutContainer { background: transparent; }
            #ShortcutsDialog QScrollBar:vertical { background: transparent; width: 10px; margin: 3px 0; }
            #ShortcutsDialog QScrollBar::handle:vertical { background: #3b473e; border-radius: 4px; min-height: 28px; }
            #ShortcutsDialog QScrollBar::add-line:vertical, #ShortcutsDialog QScrollBar::sub-line:vertical { height: 0; }
            """
        )

    def _apply_filter(self, text: str) -> None:
        query = text.strip()
        visible = sum(section.apply_filter(query) for section in self.sections)
        suffix = "SHORTCUT" if visible == 1 else "SHORTCUTS"
        self.result_count.setText(f"{visible} {suffix}")
