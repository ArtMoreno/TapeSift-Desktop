"""One read-only, asynchronous source frame for the featured Home project."""

from pathlib import Path
import sqlite3

from PySide6.QtCore import QCoreApplication, QObject, QProcess, QTimer, Signal
from PySide6.QtGui import QPixmap

from tapesift.services import ffmpeg_service


class HomePreviewLoader(QObject):
    """Connect ``ready`` before ``start``; call ``cancel`` when replacing a card."""

    ready = Signal(QPixmap)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process = QProcess(self)
        self._process.setStandardInputFile(QProcess.nullDevice())
        self._process.finished.connect(self._finished)
        self._process.errorOccurred.connect(self._failed)
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self.cancel)
        self._started = False
        self._cancelled = False
        if parent is not None:
            parent.destroyed.connect(self.cancel)
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.cancel)

    def start(self, info, ffmpeg_path=""):
        if self._started:
            return
        self._started = True
        try:
            conn = sqlite3.connect(info.path.resolve().as_uri() + "?mode=ro",
                                   uri=True, timeout=.2)
            try:
                project = conn.execute(
                    "SELECT source_video_path FROM projects LIMIT 1").fetchone()
                clip = conn.execute(
                    "SELECT start_ms, end_ms FROM clips "
                    "WHERE replace(thumbnail_path, char(92), '/') = ? LIMIT 1",
                    (info.thumbnail.replace("\\", "/"),)).fetchone()
                if clip is None:
                    clip = conn.execute(
                        "SELECT start_ms, end_ms FROM clips ORDER BY start_ms LIMIT 1").fetchone()
            finally:
                conn.close()
            source = Path(project[0]) if project and project[0] else None
            if source is None or not source.is_file() or clip is None:
                return
            time_ms = max(0, (int(clip[0]) + int(clip[1])) // 2)
        except (OSError, sqlite3.Error, ValueError, TypeError):
            return
        executable = ffmpeg_service.find_executable("ffmpeg", ffmpeg_path)
        if not executable:
            return
        command = ffmpeg_service.build_thumbnail_command(
            executable, source, Path("pipe:1"), time_ms, width=1920)
        # The shared command normally writes a JPEG file; keep its seek/scale
        # behavior but send the single image to Qt without changing any cache.
        update = command.index("-update")
        del command[update:update + 2]
        command[-1:] = ["-f", "image2pipe", "-c:v", "mjpeg", "pipe:1"]
        self._timeout.start(15000)
        self._process.start(command[0], command[1:])

    def _finished(self, exit_code, exit_status):
        self._timeout.stop()
        if self._cancelled or exit_code != 0 or exit_status != QProcess.ExitStatus.NormalExit:
            return
        pixmap = QPixmap()
        if pixmap.loadFromData(self._process.readAllStandardOutput()):
            self.ready.emit(pixmap)

    def _failed(self, _error):
        self._timeout.stop()

    def cancel(self, *_):
        self._cancelled = True
        self._timeout.stop()
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.kill()
            self._process.waitForFinished(1000)
