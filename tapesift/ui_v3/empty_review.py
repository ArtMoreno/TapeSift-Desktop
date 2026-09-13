"""Zero-play presentation layered over the authoritative film surface."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from tapesift.ui_v3.icons import icon


class ZeroPlayOverlayV3(QFrame):
    """A presentation-only doorway into existing Detect and New Clip actions."""

    def __init__(
            self, *, film_surface: QWidget,
            detect_source: QPushButton, new_clip_source: QPushButton) -> None:
        super().__init__(film_surface)
        self.setObjectName("V3ZeroPlayOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._film_surface = film_surface
        self._detect_source = detect_source
        self._new_clip_source = new_clip_source
        film_surface.installEventFilter(self)

        shell = QVBoxLayout(self)
        shell.setContentsMargins(28, 28, 28, 28)
        shell.addStretch(1)

        card = QFrame(self)
        card.setObjectName("V3ZeroPlayCard")
        card.setFixedSize(520, 320)
        column = QVBoxLayout(card)
        column.setContentsMargins(38, 30, 38, 28)
        column.setSpacing(0)

        mark = QLabel(card)
        mark.setObjectName("V3ZeroPlayIcon")
        mark.setPixmap(
            icon("scan-object-accent-20.svg").pixmap(QSize(32, 32)))
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(58, 58)
        column.addWidget(mark, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addSpacing(15)

        title = QLabel("Ready to find plays", card)
        title.setObjectName("V3ZeroPlayTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(title)
        column.addSpacing(7)

        copy = QLabel(
            "The film is loaded. Run detection to build the ledger, or mark "
            "a clip manually without leaving the player.", card)
        copy.setObjectName("V3ZeroPlayCopy")
        copy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        copy.setWordWrap(True)
        column.addWidget(copy)
        column.addSpacing(20)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.detect_button = QPushButton("Detect Plays", card)
        self.detect_button.setObjectName("V3ZeroDetectPlays")
        self.detect_button.setIcon(icon("scan-object-20.svg"))
        self.detect_button.setProperty("primary", "true")
        self.detect_button.clicked.connect(detect_source.click)
        self.new_clip_button = QPushButton("New Clip", card)
        self.new_clip_button.setObjectName("V3ZeroNewClip")
        self.new_clip_button.setIcon(icon("add-square-20.svg"))
        self.new_clip_button.clicked.connect(new_clip_source.click)
        actions.addWidget(self.detect_button)
        actions.addWidget(self.new_clip_button)
        column.addLayout(actions)
        column.addSpacing(20)

        shortcut_row = QHBoxLayout()
        shortcut_row.setSpacing(7)
        shortcut_row.addStretch(1)
        for key, label in (
                ("I", "mark in"), ("O", "mark out"),
                ("A", "add clip")):
            chip = QLabel(key, card)
            chip.setObjectName("V3ZeroShortcutKey")
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chip.setFixedSize(25, 22)
            text = QLabel(label, card)
            text.setObjectName("V3ZeroShortcutLabel")
            shortcut_row.addWidget(chip)
            shortcut_row.addWidget(text)
            if key != "A":
                divider = QLabel("·", card)
                divider.setObjectName("V3ZeroShortcutDivider")
                shortcut_row.addWidget(divider)
        shortcut_row.addStretch(1)
        column.addLayout(shortcut_row)
        column.addSpacing(16)

        privacy = QLabel(
            "Detection and playback stay on this computer.", card)
        privacy.setObjectName("V3ZeroPrivacy")
        privacy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(privacy)

        shell.addWidget(card, 0, Qt.AlignmentFlag.AlignHCenter)
        shell.addStretch(1)
        self.hide()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self._film_surface and event.type() in {
                QEvent.Type.Resize, QEvent.Type.Show}:
            self.setGeometry(self._film_surface.rect())
            self.raise_()
        return super().eventFilter(watched, event)

    def set_zero_mode(self, enabled: bool) -> None:
        enabled = bool(enabled)
        self.detect_button.setEnabled(self._detect_source.isEnabled())
        self.new_clip_button.setEnabled(self._new_clip_source.isEnabled())
        self.setGeometry(self._film_surface.rect())
        self.setVisible(enabled)
        if enabled:
            self.raise_()
