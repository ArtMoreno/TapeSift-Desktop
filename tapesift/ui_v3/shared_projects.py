"""Personal handoffs through a local folder managed by the user's sync app."""
from __future__ import annotations

import os
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, QLockFile
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from tapesift.services.project_service import ProjectSession
from tapesift.services.project_sync_service import SyncStore, SyncError


def local_key(path) -> str:
    return os.path.normcase(str(Path(path).resolve()))


class _PublishWorker(QThread):
    def __init__(self, path, link, stamp, *, check_only=False):
        super().__init__()
        self.path, self.link, self.stamp = path, dict(link), stamp
        self.check_only = check_only
        self.revision = None
        self.message = ""
        self.error = False

    def run(self):
        try:
            store = SyncStore(Path(self.link["folder"]))
            if self.link.get("project_id"):
                state = store.status(self.link["project_id"], self.link.get("revision"))
                if state.diverged:
                    raise SyncError("Multiple shared versions need review. Open Shared Projects to choose one.")
                if state.has_updates:
                    raise SyncError("A newer shared version is available. Your local work is kept; open Shared Projects.")
            if self.check_only:
                self.message = "Shared version is current"
            else:
                self.revision = store.publish(Path(self.path),
                    project_id=self.link.get("project_id"), parent_revision=self.link.get("revision"))
                self.message = "Saved to shared folder"
        except Exception as exc:
            self.error = True
            self.message = str(exc)


class SharedProjects(QObject):
    changed = Signal()

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.worker = None
        self.dialog = None
        self.edit_lock = None
        self._edit_lock_key = ""
        self.message = "Choose a synced folder in Shared Projects"
        self.error = False
        self._observed = {}
        self._published = {}
        self._last_check = 0.0
        self._pending = set()
        self._retry_after = {}
        self.status_button = QPushButton("Shared projects")
        self.status_button.setFlat(True)
        self.status_button.clicked.connect(self.show)
        window.statusBar().addPermanentWidget(self.status_button)
        self.timer = QTimer(self)
        self.timer.setInterval(10000)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self._set_message(self.message)

    @property
    def settings(self):
        return self.window.settings

    def binding(self, path):
        links = self.settings.shared_project_links
        return links.get(local_key(path)) if isinstance(links, dict) else None

    def is_shared_path(self, path):
        folders = {self.settings.shared_projects_folder}
        folders.update(link.get("folder", "") for link in self.settings.shared_project_links.values())
        return any(folder and Path(path).resolve().is_relative_to(Path(folder).resolve()) for folder in folders)

    def lock_for(self, session, *, force=False):
        if session.read_only or (not force and not self.binding(session.db_path)):
            return None
        if self.edit_lock and self._edit_lock_key == local_key(session.db_path):
            return self.edit_lock
        lock = QLockFile(str(session.db_path) + ".editing.lock")
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            raise SyncError("This local project is already being edited in another TapeSift window. Close it there first.")
        return lock

    def activated(self):
        session = self.window.session
        self._edit_lock_key = local_key(session.db_path) if session and self.edit_lock else ""
        linked = session and self.binding(session.db_path)
        self._set_message("Shared project · editing locally" if linked else "This project is local only")

    def remember(self, key, link):
        old = self.settings.shared_project_links
        self.settings.shared_project_links = dict(old, **{key: link})
        try:
            self.settings.save()
        except Exception:
            self.settings.shared_project_links = old
            raise

    def queue_saved_file(self, path):
        key = local_key(path)
        link = self.binding(path)
        if link:
            self._pending.add(key)
            self.remember(key, dict(link, pending="1"))
            self._set_message("Saved locally · waiting to share")

    def library_edit(self, path, apply):
        lock = None
        try:
            if self.is_shared_path(path):
                return False, "Open a local copy from Shared Projects before editing this saved version."
            session = self.window.session
            active = session and local_key(session.db_path) == local_key(path)
            if active and session.read_only:
                return False, "This project is open read-only."
            if self.binding(path) and not active:
                lock = QLockFile(str(path) + ".editing.lock")
                lock.setStaleLockTime(0)
                if not lock.tryLock(0):
                    return False, "This project is open in another TapeSift window. Close it there first."
            result = apply()
            if result[0]:
                try:
                    self.queue_saved_file(path)
                except Exception as exc:
                    return False, f"Saved locally, but sharing could not be queued: {exc}"
            return result
        finally:
            if lock is not None:
                lock.unlock()

    def moved(self, old_path, new_path):
        old_key = local_key(old_path)
        link = self.binding(old_path)
        if not link:
            return
        links = dict(self.settings.shared_project_links)
        links.pop(old_key, None)
        self._pending.discard(old_key)
        if new_path:
            key = local_key(new_path)
            links[key] = dict(link, pending="1")
            self._pending.add(key)
        self.settings.shared_project_links = links
        try:
            self.settings.save()
        except Exception as exc:
            # The file already moved. Keep its new identity usable this session.
            self._set_message(f"Project renamed; its shared link could not be remembered: {exc}", error=True)
        if self.edit_lock and self._edit_lock_key == old_key:
            self.edit_lock.unlock()
            self.edit_lock = None
            self._edit_lock_key = ""

    def _stamp(self, session):
        return (session.project.updated_at, session.conn.total_changes)

    def _set_message(self, message, *, error=False):
        self.message, self.error = message, error
        self.status_button.setText("Shared: needs attention" if error else message)
        self.status_button.setToolTip(message + "\nBefore switching computers, wait for your drive app to finish syncing.")
        self.status_button.setVisible(bool(self.settings.shared_projects_folder or self.settings.shared_project_links))
        self.changed.emit()

    def show(self):
        if self.dialog is None:
            self.dialog = SharedProjectsDialog(self)
        self.dialog.refresh()
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def poll(self):
        session = self.window.session
        if self.worker:
            return
        self._pending.update(key for key, link in self.settings.shared_project_links.items() if link.get("pending") == "1")
        ready = [key for key in sorted(self._pending) if time.monotonic() >= self._retry_after.get(key, 0)]
        if ready:
            key = ready[0]
            self._pending.discard(key)
            link = self.binding(key)
            if link:
                self._start_path(key, link, None)
                return
        if not session or session.read_only:
            return
        key = local_key(session.db_path)
        link = self.binding(session.db_path)
        if not link:
            return
        stamp = self._stamp(session)
        if self._observed.get(key) != stamp:
            self._observed[key] = stamp
            self._set_message("Saved locally · waiting to share")
            return
        if self._published.get(key) != stamp:
            if not self.error or time.monotonic() - self._last_check > 30:
                self.publish()
        elif time.monotonic() - self._last_check > 30:
            self._start(session, link, check_only=True)

    def save_draft(self) -> bool:
        editor = self.window.clip_editor
        if editor._clip and editor.save_state_label.property("state") == "dirty":
            if not editor._apply():
                return False
        return self.window._try_save()

    def publish(self, *, new=False, save_draft=False):
        session = self.window.session
        if self.worker or not session or session.read_only:
            return False
        if self.edit_lock is None:
            try:
                self.edit_lock = self.lock_for(session, force=True)
                self._edit_lock_key = local_key(session.db_path)
            except SyncError as exc:
                self._set_message(str(exc), error=True)
                return False
        if save_draft and not self.save_draft():
            return False
        link = None if new else self.binding(session.db_path)
        if link is None:
            link = {"folder": self.settings.shared_projects_folder}
        if not link.get("folder"):
            self._set_message("Choose your synced folder first", error=True)
            return False
        link = dict(link, source_path=session.project.source_video_path)
        self._start(session, link)
        return True

    def _start(self, session, link, *, check_only=False):
        self._start_path(str(session.db_path), link, self._stamp(session), check_only=check_only)

    def _start_path(self, path, link, stamp, *, check_only=False):
        self._pending.discard(local_key(path))
        self.worker = _PublishWorker(path, link, stamp, check_only=check_only)
        worker = self.worker
        worker.finished.connect(lambda: self._finished(worker))
        self._last_check = time.monotonic()
        self._set_message("Checking shared version…" if check_only else "Saving to shared folder…")
        worker.start()

    def _finished(self, worker):
        if self.worker is not worker:
            return
        key = local_key(worker.path)
        if worker.revision is not None:
            revision = worker.revision
            try:
                self.remember(key, dict(worker.link, project_id=revision.project_id,
                                       revision=revision.revision_id, pending="1" if key in self._pending else ""))
            except Exception as exc:
                worker.error = True
                worker.message = f"Version saved, but this computer could not remember its link: {exc}"
            self._published[key] = worker.stamp
            self._observed[key] = worker.stamp
        self.worker = None
        if worker.error:
            self._retry_after[key] = time.monotonic() + 30
        active = self.window.session and local_key(self.window.session.db_path) == key
        message = worker.message if active else "Another project: " + worker.message
        self._set_message(message, error=worker.error)
        worker.deleteLater()

    def wait(self) -> bool:
        worker = self.worker
        if worker is None:
            return True
        if not worker.wait(10000):
            self._set_message("Still saving to the shared folder. Try closing again when it finishes.", error=True)
            return False
        self._finished(worker)
        return True

    def before_close(self) -> bool:
        if not self.wait():
            return False
        # Closed-project Library edits also need to finish before app exit.
        for key, link in list(self.settings.shared_project_links.items()):
            if link.get("pending") == "1":
                self._start_path(key, link, None)
                if not self.wait():
                    return False
                if self.error and QMessageBox.question(self.window, "Saved locally",
                    self.message + "\n\nClose without sharing this saved version?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                    return False
        session = self.window.session
        if not session or session.read_only or not self.binding(session.db_path):
            return True
        editor = self.window.clip_editor
        if editor._clip and editor.save_state_label.property("state") == "dirty":
            choice = QMessageBox.question(self.window, "Save play details?",
                "Save the pending play details before sharing and closing?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save)
            if choice == QMessageBox.StandardButton.Cancel:
                return False
            if choice == QMessageBox.StandardButton.Save and not self.save_draft():
                return False
        if not self.window._try_save() or not self.publish() or not self.wait():
            return False
        if self.error:
            return QMessageBox.question(self.window, "Saved locally",
                self.message + "\n\nYour project is saved on this computer. Close without sharing this version?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes
        return True

    def open_revision(self, folder, revision, *, latest=False):
        if not self.window._close_project():
            return False
        store = SyncStore(Path(folder))
        if latest:
            state = store.status(revision.project_id)
            if state.latest is None:
                raise SyncError("The shared version changed. Refresh and choose a saved version.")
            revision = state.latest
        target = store.checkout(revision.project_id, revision.revision_id,
                                Path(self.settings.default_project_folder))
        session = ProjectSession.open(target)
        try:
            # Video and export locations belong to this computer, never to the
            # machine that last uploaded the play details.
            links = self.settings.shared_project_links.values()
            previous = next((link for link in reversed(list(links))
                if link.get("project_id") == revision.project_id
                and link.get("source_path") and Path(link["source_path"]).is_file()), None)
            if previous:
                session.project.source_video_path = previous["source_path"]
                session.project.source_metadata.path = previous["source_path"]
            session.project.output_folder = self.settings.default_output_folder
            session.save()
            self.remember(local_key(target), {
                "folder": folder, "project_id": revision.project_id, "revision": revision.revision_id,
                "source_path": session.project.source_video_path,
            })
            if not self.window._activate_session(session):
                session.conn.close()
                return False
        except Exception:
            if self.window.session is not session:
                session.conn.close()
            raise
        self._published[local_key(target)] = self._stamp(session)
        self._observed[local_key(target)] = self._stamp(session)
        self._set_message("Shared version opened · editing locally")
        return True


class SharedProjectsDialog(QDialog):
    def __init__(self, controller):
        super().__init__(controller.window)
        self.controller = controller
        self.setWindowTitle("Shared Projects — Personal sync")
        self.resize(700, 500)
        layout = QVBoxLayout(self)
        intro = QLabel("Choose a dedicated folder in your cloud drive on each computer. Edit on one computer at a time.\n"
            "Play details and saved images travel with each version; keep the original film available separately.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        folder_row = QHBoxLayout()
        self.folder_edit = QLineEdit()
        self.folder_edit.setReadOnly(True)
        self.folder_edit.setPlaceholderText("Your private synced folder")
        self.folder_edit.setAccessibleName("Shared projects folder")
        folder_row.addWidget(self.folder_edit, 1)
        choose = QPushButton("Choose folder…")
        choose.clicked.connect(self.choose_folder)
        folder_row.addWidget(choose)
        layout.addLayout(folder_row)
        self.versions = QTreeWidget()
        self.versions.setHeaderLabels(["Project / saved version", "Saved (UTC)", "Status"])
        self.versions.setColumnWidth(0, 320)
        self.versions.setColumnWidth(1, 170)
        self.versions.setRootIsDecorated(True)
        self.versions.itemSelectionChanged.connect(self.sync_buttons)
        layout.addWidget(self.versions, 1)
        actions = QHBoxLayout()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        actions.addWidget(refresh)
        self.open_button = QPushButton("Open saved version")
        self.open_button.clicked.connect(self.open_selected)
        actions.addWidget(self.open_button)
        actions.addStretch()
        self.save_button = QPushButton("Save current to shared folder")
        self.save_button.clicked.connect(lambda: controller.publish(save_draft=True))
        actions.addWidget(self.save_button)
        layout.addLayout(actions)
        self.new_button = QPushButton("Share current as a new project")
        self.new_button.clicked.connect(lambda: controller.publish(new=True, save_draft=True))
        layout.addWidget(self.new_button)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        note = QLabel("Before switching computers, wait for your drive app to finish syncing.\n"
            "If versions overlap, both are kept. Open the version you want and share it as a new project to continue.")
        note.setWordWrap(True)
        layout.addWidget(note)
        controller.changed.connect(self.update_status)

    def update_status(self):
        self.message.setText(self.controller.message)
        self.sync_buttons()

    def sync_buttons(self):
        busy = self.controller.worker is not None
        session = self.controller.window.session
        writable = bool(session and not session.read_only and self.folder_edit.text())
        link = self.controller.binding(session.db_path) if session else None
        same_folder = not link or local_key(link["folder"]) == local_key(self.folder_edit.text() or ".")
        self.save_button.setEnabled(writable and not busy and same_folder)
        self.save_button.setToolTip("This project is linked to " + link["folder"] if link and not same_folder else "Publish saved play details to this folder")
        self.new_button.setEnabled(writable and not busy)
        item = self.versions.currentItem()
        self.open_button.setEnabled(bool(item and item.data(0, Qt.ItemDataRole.UserRole)) and not busy)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose your private synced folder",
                                                  self.controller.settings.shared_projects_folder)
        if not folder:
            return
        previous = self.controller.settings.shared_projects_folder
        self.controller.settings.shared_projects_folder = folder
        try:
            self.controller.settings.save()
        except Exception as exc:
            self.controller.settings.shared_projects_folder = previous
            self.controller._set_message(str(exc), error=True)
            return
        self.refresh()

    def refresh(self):
        folder = self.controller.settings.shared_projects_folder
        self.folder_edit.setText(folder)
        self.versions.clear()
        if folder:
            try:
                store = SyncStore(Path(folder))
                for project in store.list_projects():
                    state = project.problem or ("Multiple versions — choose below" if len(project.heads) > 1 else "Latest saved version")
                    top = QTreeWidgetItem([project.name, "", state])
                    self.versions.addTopLevelItem(top)
                    if project.latest:
                        top.setData(0, Qt.ItemDataRole.UserRole, project.latest)
                    for revision in sorted(store.revisions(project.project_id, complete_only=True), key=lambda r: r.created_at, reverse=True):
                        item = QTreeWidgetItem([revision.name, revision.created_at[:19].replace("T", " "),
                            "Latest" if revision in project.heads else "Earlier version"])
                        item.setData(0, Qt.ItemDataRole.UserRole, revision)
                        top.addChild(item)
                    if len(project.heads) > 1:
                        top.setExpanded(True)
            except Exception as exc:
                self.controller._set_message(str(exc), error=True)
        self.update_status()

    def open_selected(self):
        item = self.versions.currentItem()
        revision = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if revision is None:
            return
        try:
            if self.controller.open_revision(self.folder_edit.text(), revision, latest=item.parent() is None):
                self.hide()
        except Exception as exc:
            self.controller._set_message(f"Could not open shared version: {exc}", error=True)
