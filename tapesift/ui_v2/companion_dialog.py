"""Start and stop the phone companion without a terminal.

The server has always worked; typing its command every time did not.
This is the same server behind a menu item: open it when you get home,
press Start, sync whatever the phone collected, press Stop.

It also owns the one rule the command line made the operator remember -
that writes are only safe when no project in the folder is open in the
desktop app. Here the app knows its own state, so the dialog can say
which project is in the way and offer to close it, rather than printing
an instruction and exiting.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout)

from tapesift.services.companion_server import (
    DEFAULT_PORT, ProjectLibrary, desktop_has_it_open, serve,
    tailscale_urls)


class CompanionDialog(QDialog):
    """Runs the companion for a folder of projects."""

    def __init__(self, folder: Path, parent=None,
                 current_project: Path | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Phone Companion")
        self.setObjectName("V2CompanionDialog")
        self.setMinimumWidth(430)

        self._folder = Path(folder)
        self._current_project = current_project
        self._server = None
        self._url = ""
        self._copy_target = ""
        self._tailscale_urls: list[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(11)

        self.heading = QLabel("Phone Companion")
        self.heading.setProperty("role", "heading")
        layout.addWidget(self.heading)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setProperty("role", "companionSummary")
        layout.addWidget(self.summary)

        self.allow_writes = QCheckBox(
            "Let the phone sync tags back into these projects")
        self.allow_writes.setToolTip(
            "Off, the phone can still tag - the edits queue on the phone.\n"
            "On, its Sync button can write them into the project files.")
        layout.addWidget(self.allow_writes)

        self.url_label = QLabel()
        self.url_label.setObjectName("CompanionUrl")
        self.url_label.setWordWrap(True)
        self.url_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.url_label.setVisible(False)
        layout.addWidget(self.url_label)

        self.tailscale_label = QLabel()
        self.tailscale_label.setObjectName("CompanionTailscaleUrl")
        self.tailscale_label.setWordWrap(True)
        self.tailscale_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.tailscale_label.setVisible(False)
        layout.addWidget(self.tailscale_label)

        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setProperty("role", "companionNote")
        self.note.setVisible(False)
        layout.addWidget(self.note)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.copy_button = QPushButton("Copy Address")
        self.copy_button.clicked.connect(self._copy_url)
        self.copy_button.setVisible(False)
        buttons.addWidget(self.copy_button)
        buttons.addStretch(1)
        self.start_button = QPushButton("Start")
        self.start_button.setDefault(True)
        self.start_button.clicked.connect(self._toggle)
        buttons.addWidget(self.start_button)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        self._refresh_summary()

    # ------------------------------------------------------------ internals

    def _library(self) -> ProjectLibrary:
        return ProjectLibrary(self._folder)

    def _blocking_projects(self) -> list[Path]:
        """Projects some process still holds open - usually this app."""
        return [p for p in self._library().files() if desktop_has_it_open(p)]

    def _refresh_summary(self) -> None:
        rows = self._library().summaries()
        if not rows:
            self.summary.setText(
                f"No projects found in {self._folder}.")
            self.start_button.setEnabled(False)
            return
        plays = sum(row["plays"] for row in rows)
        missing = [r["name"] for r in rows if not r["film_available"]]
        text = (f"{len(rows)} projects, {plays} plays. "
                "The phone gets all of them and picks between them.")
        if missing:
            text += (f"\n{len(missing)} without their source film - those "
                     "list without video.")
        self.summary.setText(text)
        self.start_button.setEnabled(True)

    def _copy_url(self) -> None:
        if self._copy_target:
            QGuiApplication.clipboard().setText(self._copy_target)
            self.copy_button.setText("Copied")

    def _toggle(self) -> None:
        if self._server is not None:
            self._stop()
        else:
            self._start()

    def _start(self) -> None:
        writes = self.allow_writes.isChecked()
        if writes:
            busy = self._blocking_projects()
            if busy:
                # Not a warning to be clicked past. A write made now is
                # erased by the next desktop save of that project, with
                # nothing to show it happened.
                names = "\n".join(f"  • {p.stem}" for p in busy[:6])
                self.note.setVisible(True)
                self.note.setProperty("tone", "bad")
                self.note.setText(
                    "These projects are open somewhere and cannot be "
                    f"written to yet:\n{names}\n\n"
                    "If one is open in this TapeSift window, close Phone "
                    "Companion, choose Close Project, then reopen Phone "
                    "Companion. If another app has it open, close it there "
                    "and press Start again. "
                    "Leaving syncing off starts the companion anyway - "
                    "the phone will queue its tags until later.")
                self.note.style().unpolish(self.note)
                self.note.style().polish(self.note)
                return

        try:
            self._server, self._url = serve(
                self._folder, port=DEFAULT_PORT, writable=writes)
        except OSError as error:
            self.note.setVisible(True)
            self.note.setProperty("tone", "bad")
            self.note.setText(
                f"Could not start on port {DEFAULT_PORT}: {error}\n"
                "Another companion may already be running.")
            return

        self.url_label.setText(self._url)
        self.url_label.setVisible(True)
        port = self._server.server_address[1]
        token = getattr(self._server.RequestHandlerClass, "token", "")
        self._tailscale_urls = tailscale_urls(port, token)
        if self._tailscale_urls:
            self.tailscale_label.setText(
                "Away from home with Tailscale:\n"
                + "\n".join(self._tailscale_urls))
            self.tailscale_label.setVisible(True)
            self._copy_target = self._tailscale_urls[0]
        else:
            self.tailscale_label.setVisible(False)
            self._copy_target = self._url
        self.copy_button.setVisible(True)
        self.copy_button.setText(
            "Copy Tailscale Address" if self._tailscale_urls
            else "Copy Address")
        self.start_button.setText("Stop")
        self.allow_writes.setEnabled(False)
        self.note.setVisible(True)
        self.note.setProperty("tone", "")
        self.note.setText(
            "Open the first address on the same Wi-Fi, or the Tailscale "
            "address anywhere your phone has Tailscale connected.\n"
            + ("Syncing is on - the phone's Sync button can write tags "
               "into these projects."
               if writes else
               "Read-only. The phone can still tag; its edits queue there "
               "until you start this with syncing on.")
            + "\nKeep this computer awake and this window open. Private "
              "network only - do not forward this port or enable Funnel.")
        self.note.style().unpolish(self.note)
        self.note.style().polish(self.note)

    def _stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        self._url = ""
        self._copy_target = ""
        self._tailscale_urls = []
        self.url_label.setVisible(False)
        self.tailscale_label.setVisible(False)
        self.copy_button.setVisible(False)
        self.start_button.setText("Start")
        self.allow_writes.setEnabled(True)
        self.note.setVisible(True)
        self.note.setProperty("tone", "")
        self.note.setText("Stopped. The phone keeps anything it has not "
                          "synced.")
        self.note.style().unpolish(self.note)
        self.note.style().polish(self.note)
        self._refresh_summary()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """A server outliving its window would keep the port and the files.

        There is no tray icon and no other way back to it, so a dialog
        closing has to take the server with it.
        """
        self._stop()
        super().closeEvent(event)

    def reject(self) -> None:  # noqa: D102
        self._stop()
        super().reject()
