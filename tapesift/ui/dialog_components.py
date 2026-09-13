"""Shared visual building blocks for TapeSift's menu dialogs."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout, QWidget,
)

_BRAND_LOCKUP = (
    Path(__file__).resolve().parent.parent
    / "resources"
    / "icons"
    / "tapesift-logo.png"
)


class DialogHeader(QWidget):
    """The same visual hierarchy used by the shortcut reference."""

    def __init__(
        self,
        title: str,
        subtitle: str,
        *,
        eyebrow: str = "TAPESIFT",
        badge: str = "",
        show_brand: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(16)
        copy = QVBoxLayout()
        copy.setSpacing(3)
        if show_brand and _BRAND_LOCKUP.is_file():
            pixmap = QPixmap(str(_BRAND_LOCKUP))
            if not pixmap.isNull():
                brand = QLabel()
                brand.setObjectName("DialogBrandLockup")
                brand.setAccessibleName("TapeSift")
                brand.setProperty("brandAsset", "tapesift-logo.png")
                brand.setPixmap(pixmap.scaledToHeight(
                    27, Qt.TransformationMode.SmoothTransformation))
                copy.addWidget(
                    brand, 0, Qt.AlignmentFlag.AlignLeft)
        kicker = QLabel(eyebrow)
        kicker.setProperty("role", "eyebrow")
        copy.addWidget(kicker)
        heading = QLabel(title)
        heading.setProperty("role", "dialogTitle")
        heading.setWordWrap(True)
        copy.addWidget(heading)
        description = QLabel(subtitle)
        description.setProperty("role", "subtle")
        description.setWordWrap(True)
        copy.addWidget(description)
        row.addLayout(copy, 1)
        if badge:
            badge_label = QLabel(badge)
            badge_label.setProperty("badge", True)
            row.addWidget(badge_label, 0, Qt.AlignmentFlag.AlignTop)


class DialogSection(QFrame):
    """A bordered content card for a related group of controls."""

    def __init__(
        self,
        title: str = "",
        subtitle: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("dialogSection", True)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 14, 16, 14)
        self.body.setSpacing(9)
        if title:
            heading = QLabel(title)
            heading.setProperty("role", "sectionTitle")
            self.body.addWidget(heading)
        if subtitle:
            description = QLabel(subtitle)
            description.setProperty("role", "subtle")
            description.setWordWrap(True)
            self.body.addWidget(description)


class ActionDialog(QDialog):
    """Consistent confirmation/notice window for menu actions."""

    def __init__(
        self,
        *,
        title: str,
        subtitle: str,
        body: str,
        confirm_text: str,
        eyebrow: str,
        cancel_text: str = "Cancel",
        checkbox_text: str = "",
        checkbox_checked: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("tapesiftDialog", True)
        self.setWindowTitle(title)
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)
        layout.addWidget(DialogHeader(
            title, subtitle, eyebrow=eyebrow, parent=self))

        section = DialogSection()
        message = QLabel(body)
        message.setProperty("role", "dialogBody")
        message.setWordWrap(True)
        section.body.addWidget(message)
        self.checkbox: QCheckBox | None = None
        if checkbox_text:
            self.checkbox = QCheckBox(checkbox_text)
            self.checkbox.setChecked(checkbox_checked)
            section.body.addWidget(self.checkbox)
        layout.addWidget(section)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        if cancel_text:
            cancel = QPushButton(cancel_text)
            cancel.clicked.connect(self.reject)
            buttons.addWidget(cancel)
        confirm = QPushButton(confirm_text)
        confirm.setProperty("primary", "true")
        confirm.setDefault(True)
        confirm.clicked.connect(self.accept)
        buttons.addWidget(confirm)
        layout.addLayout(buttons)
