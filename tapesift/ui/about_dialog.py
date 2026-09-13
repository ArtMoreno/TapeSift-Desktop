"""Product version and dependency information."""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from tapesift import AUTHOR_LINE, COPYRIGHT_LINE, __version__
from tapesift.ui.dialog_components import DialogHeader, DialogSection

QT_URL = "https://doc.qt.io/qtforpython-6/"
FFMPEG_URL = "https://ffmpeg.org/"


class AboutDialog(QDialog):
    def __init__(self, ffmpeg_version: str, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("tapesiftDialog", True)
        self.setWindowTitle("About TapeSift")
        self.setMinimumSize(640, 480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)
        layout.addWidget(DialogHeader(
            "About TapeSift", "Football film review and clip editing.",
            eyebrow="APPLICATION", badge=f"VERSION {__version__}",
            show_brand=True, parent=self))
        product = DialogSection("TapeSift", AUTHOR_LINE)
        description = QLabel(
            "Review film, organize plays, and export clips on Windows and Linux. "
            "Editing and play detection run locally. Optional First Read sends "
            "selected frame contact sheets to the configured provider only when run.")
        description.setWordWrap(True)
        product.body.addWidget(description)
        layout.addWidget(product)
        dependency = DialogSection(
            "Media engine", "Qt and PySide6 provide the interface and playback. "
            "FFmpeg runs as a separate program. Windows bundles use LGPL v3 or later "
            "builds; Linux uses the installed FFmpeg. See THIRD_PARTY_NOTICES.txt.")
        version = QLabel(ffmpeg_version)
        version.setProperty("mono", "true")
        version.setWordWrap(True)
        dependency.body.addWidget(version)
        links = QHBoxLayout()
        for name, title, url in (("DependencyQtLink", "Qt documentation", QT_URL),
                                  ("DependencyFFmpegLink", "FFmpeg", FFMPEG_URL)):
            button = QPushButton(title)
            button.setObjectName(name)
            button.setAccessibleName("Open " + title)
            button.setToolTip("Opens in the default web browser")
            button.setProperty("quiet", "true")
            button.clicked.connect(lambda _checked=False, target=url:
                                   QDesktopServices.openUrl(QUrl(target)))
            links.addWidget(button)
        links.addStretch(1)
        dependency.body.addLayout(links)
        layout.addWidget(dependency)
        layout.addStretch(1)
        footer = QHBoxLayout()
        copyright_label = QLabel(COPYRIGHT_LINE)
        copyright_label.setProperty("role", "subtle")
        footer.addWidget(copyright_label, 1)
        done = QPushButton("Done")
        done.setProperty("primary", "true")
        done.setDefault(True)
        done.clicked.connect(self.accept)
        footer.addWidget(done, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(footer)
