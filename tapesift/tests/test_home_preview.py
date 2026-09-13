"""The Home hero decodes asynchronously without replacing project thumbnails."""

import sqlite3
import sys

from PySide6.QtCore import QProcess
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QWidget

from tapesift.ui_core.start_screen import ProjectInfo
from tapesift.ui_v3.home_preview import HomePreviewLoader


def test_source_preview_uses_matching_clip_and_preserves_files(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "film.mp4"
    source.write_bytes(b"source stays untouched")
    thumbnail = tmp_path / "cached.jpg"
    thumbnail.write_bytes(b"cached thumbnail stays untouched")
    project = tmp_path / "project.tapesift"
    with sqlite3.connect(project) as conn:
        conn.executescript("CREATE TABLE projects (source_video_path TEXT);"
                           "CREATE TABLE clips (start_ms INTEGER, end_ms INTEGER, thumbnail_path TEXT);")
        conn.execute("INSERT INTO projects VALUES (?)", (str(source),))
        conn.execute("INSERT INTO clips VALUES (0, 100, 'other.jpg')")
        conn.execute("INSERT INTO clips VALUES (1000, 3000, ?)", (thumbnail.as_posix(),))
    original = [path.read_bytes() for path in (project, source, thumbnail)]
    info = ProjectInfo(path=project, thumbnail=str(thumbnail))
    monkeypatch.setattr("tapesift.ui_v3.home_preview.ffmpeg_service.find_executable",
                        lambda *_: sys.executable)

    def image_command(executable, chosen_source, output, time_ms, width):
        assert chosen_source == source and time_ms == 2000 and width == 1920
        return [executable, "-c", "import sys; sys.stdout.buffer.write(b'P6\\n8 4\\n255\\n' + bytes([80, 120, 160]) * 32)",
                "-update", "1", str(output)]

    monkeypatch.setattr("tapesift.ui_v3.home_preview.ffmpeg_service.build_thumbnail_command", image_command)
    parent = QWidget()
    loader = HomePreviewLoader(parent)
    ready = QSignalSpy(loader.ready)
    loader.start(info)
    assert ready.wait(5000)
    assert ready.at(0)[0].width() == 8
    assert [path.read_bytes() for path in (project, source, thumbnail)] == original

    monkeypatch.setattr("tapesift.ui_v3.home_preview.ffmpeg_service.build_thumbnail_command",
                        lambda *args, **kwargs: [sys.executable, "-c", "import time; time.sleep(20)",
                                                "-update", "1", "pipe:1"])
    pending = HomePreviewLoader(parent)
    cancelled_ready = QSignalSpy(pending.ready)
    pending.start(info)
    assert pending._process.waitForStarted(3000)
    pending.cancel()
    assert pending._process.state() == QProcess.ProcessState.NotRunning
    assert cancelled_ready.count() == 0
    parent.close()
