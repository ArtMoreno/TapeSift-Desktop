"""Exercise the current desktop with generated footage and an isolated profile."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    os.environ["APPDATA"] = str(out / "profile")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from tapesift.core.config import AppSettings
    from tapesift.models.clip import Clip
    from tapesift.models.video_metadata import VideoMetadata
    from tapesift.models.export_settings import SOURCE_QUALITY
    from tapesift.services import ffmpeg_service
    from tapesift.services.export_service import export_clip, FFmpegRunner
    from tapesift.services.project_service import ProjectSession
    from tapesift.ui_v2.fonts import load_v2_fonts
    from tapesift.ui_v3.main_window import MainWindowV3
    from tapesift.ui_v3.theme import stylesheet

    ffmpeg = ffmpeg_service.find_executable("ffmpeg")
    ffprobe = ffmpeg_service.find_executable("ffprobe")
    assert ffmpeg and ffprobe, "FFmpeg and FFprobe are required"
    source = out / "sample-film.mp4"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
                    "-t", "12", "-c:v", "mpeg4", "-q:v", "3", str(source)],
                   check=True, timeout=60)
    settings = AppSettings(onboarding_seen=True, scrub_proxy_enabled=False,
                           default_project_folder=str(out / "projects"),
                           default_output_folder=str(out / "exports"),
                           ffmpeg_path=ffmpeg, ffprobe_path=ffprobe)
    app = QApplication([])
    load_v2_fonts()
    app.setStyleSheet(stylesheet())
    window = MainWindowV3(settings, workspace_state_path=out / "workspace.json")
    window._screen_fit_done = True
    window.resize(1500, 900)
    window.show()
    result = {"platform": sys.platform, "qt_platform": app.platformName()}
    session = None
    def wait_for(predicate, message, seconds=12):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            QTest.qWait(50)
            if predicate():
                return
        raise AssertionError(message)
    try:
        QTest.qWait(300)
        window.grab().save(str(out / "home.png"))
        session = ProjectSession.create("Sample Film", out / "projects", out / "exports")
        session.project.source_video_path = str(source)
        session.project.source_duration_ms = 12000
        session.project.source_metadata = VideoMetadata(
            path=str(source), duration_ms=12000, width=640, height=360, frame_rate=30)
        clip = session.add_clip(Clip(0, 6000, clip_title="Sample Play", details={
            "quarter": "Q1", "down_distance": "1st & 10", "run_pass": "Pass", "result": "Completion"}))
        session.add_clip(Clip(6000, 11000, clip_title="Second Play", details={"run_pass": "Run"}))
        session.save()
        assert window._activate_session(session)
        window.autosave_timer.stop()
        player = window.player
        wait_for(lambda: player.player.duration() > 0, "Video did not load")
        player.toggle_play()
        wait_for(lambda: (player.displayed_position_ms() or 0) > 500, "No advancing video frames")
        player.shuttle_stop()
        samples = []
        for _ in range(3):
            before = player.displayed_position_ms()
            player.toggle_play()
            wait_for(lambda: (player.displayed_position_ms() or 0) > before + 200,
                     "Play after pause did not advance")
            player.shuttle_stop()
            QTest.qWait(150)
            samples.append(player.displayed_position_ms())
        before = player.displayed_position_ms()
        player.frame_step_forward()
        wait_for(lambda: (player.displayed_position_ms() or 0) > before, "Forward frame step failed")
        forward = player.displayed_position_ms()
        player.frame_step_backward()
        wait_for(lambda: player.displayed_position_ms() is not None and player.displayed_position_ms() < forward,
                 "Backward frame step failed")
        player.shuttle_forward()
        wait_for(lambda: (player.displayed_position_ms() or 0) > forward + 250, "Forward shuttle failed")
        player.shuttle_stop()
        before_reverse = player.displayed_position_ms()
        player.shuttle_reverse()
        wait_for(lambda: player.displayed_position_ms() is not None and player.displayed_position_ms() < before_reverse - 100,
                 "Reverse shuttle failed")
        player.shuttle_stop()
        window._set_ledger_open(True)
        window._set_details_open(True)
        QTest.qWait(300)
        window.grab().save(str(out / "review.png"))
        destination = out / "sample-export.mp4"
        export_clip(ffmpeg, session.project, clip, destination, SOURCE_QUALITY, True, FFmpegRunner())
        metadata = json.loads(subprocess.check_output([
            ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(destination)]))
        duration = float(metadata["format"]["duration"])
        assert abs(duration - 6.0) < .2, duration
        db = session.db_path
        session.save()
        reread = ProjectSession.open_read_only(db)
        try:
            assert len(reread.clips) == 2
            assert reread.clips[0].details["quarter"] == "Q1"
        finally:
            reread.close()
        result.update(passed=True, pause_resume_positions=samples,
                      forward_step_ms=forward-before, export_duration_seconds=duration)
        print(json.dumps(result), flush=True)
        return 0
    finally:
        (out / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        window._app_closing = True
        window.autosave_timer.stop()
        window.player.unload()
        if session is not None:
            session.close()
        window.hide()
        window.deleteLater()
        from PySide6.QtCore import QEvent
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


if __name__ == "__main__":
    raise SystemExit(main())
