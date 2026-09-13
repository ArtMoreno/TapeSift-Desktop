"""First-run welcome: what TapeSift is, and the three steps to first clips.

Shown once, on the first launch of a fresh install. Everything here is
written for someone who has never seen the app and does not know what
"All-22", "proxy" or "in/out points" mean in this context - the reader is
a coach with a game file, not a video editor.

Deliberately not a multi-page tour. A tour is skipped; a single screen
that names the three things to do actually gets read.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from tapesift.ui.dialog_components import DialogHeader, DialogSection

STEPS = [
    ("1. Open your film",
     "Drag a game video onto the TapeSift window, or click New Project. "
     "MP4, MOV, MKV, AVI, M4V and WebM all work."),
    ("2. Let it find the plays",
     "Click <b>Detect Plays (Beta)</b> in the Clip Ledger. On All-22 cut-up film, "
     "where each "
     "play is separated by a black gap, a scoreboard card or a hard cut - "
     "TapeSift finds the plays for you and makes a clip of each one. You "
     "see the full list before anything is created."),
    ("3. Log and export",
     "Use <b>W/B</b> for the next/previous clip outside text fields, and <b>E</b> to edit Clip Details. For each play, enter the "
     "details - player, down &amp; distance, result - and the clip names "
     "itself. Then export individual clips, a combined reel, or both."),
]

FOOTER = (
    "No film that's already cut up? Mark plays by hand instead: press "
    "<b>I</b> at the start and <b>O</b> at the end. With default prompts, O opens "
    "the details form; with prompts off, press <b>A</b> to name the clip.<br><br>"
    "Editing, playback and detection run locally. Optional First Read sends selected "
    "frame contact sheets to your configured provider only when you run it. "
    "Press <b>F1</b> for the keyboard guide."
)


class WelcomeDialog(QDialog):
    """One screen, three steps, then out of the way."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("tapesiftDialog", True)
        self.setWindowTitle("Welcome to TapeSift")
        self.setMinimumSize(680, 640)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)

        layout.addWidget(DialogHeader(
            "From full game to searchable clips.",
            "TapeSift finds the plays, keeps the logging organized, and "
            "exports the cutups-all from your desktop.",
            eyebrow="GETTING STARTED",
            badge="3 STEPS",
            show_brand=True,
            parent=self,
        ))

        for index, (title, body) in enumerate(STEPS, start=1):
            layout.addWidget(self._step(index, title.split(". ", 1)[-1], body))

        note = DialogSection("Manual marking and privacy")
        footer = QLabel(FOOTER)
        footer.setWordWrap(True)
        footer.setProperty("role", "subtle")
        note.body.addWidget(footer)
        layout.addWidget(note)
        layout.addStretch(1)

        buttons = QHBoxLayout()
        self.show_again = QCheckBox("Show this again next time")
        buttons.addWidget(self.show_again)
        buttons.addStretch()
        start = QPushButton("Get Started")
        start.setProperty("primary", "true")
        start.setMinimumHeight(34)
        start.setMinimumWidth(130)
        start.setDefault(True)
        start.clicked.connect(self.accept)
        buttons.addWidget(start)
        layout.addLayout(buttons)

    @staticmethod
    def _step(index: int, title: str, body: str) -> QWidget:
        panel = DialogSection()
        heading_row = QHBoxLayout()
        number = QLabel(f"0{index}")
        number.setProperty("stepNumber", "true")
        number.setMinimumWidth(30)
        number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading_row.addWidget(number)
        heading = QLabel(title)
        heading.setProperty("role", "sectionTitle")
        heading_row.addWidget(heading, 1)
        panel.body.addLayout(heading_row)
        text = QLabel(body)
        text.setWordWrap(True)
        text.setProperty("role", "subtle")
        panel.body.addWidget(text)
        return panel


def show_if_first_run(settings, parent=None) -> bool:
    """Show the welcome screen once. Returns True if it was shown.

    The flag is saved whichever way the dialog is dismissed - closing it
    with the X still counts as having seen it, otherwise it reappears
    every launch for anyone who doesn't click the button.
    """
    if settings.onboarding_seen:
        return False
    dialog = WelcomeDialog(parent)
    dialog.exec()
    settings.onboarding_seen = not dialog.show_again.isChecked()
    settings.save()
    return True
