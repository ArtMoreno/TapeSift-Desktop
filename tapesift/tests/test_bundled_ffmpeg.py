"""Shipping FFmpeg inside TapeSift.

The bundled build is LGPL, which is what makes it redistributable with
software we sell - and LGPL means **no x264**. Every default preset and
the scrub proxy asked for libx264 by name, so bundling without this
substitution layer produces an app that installs cleanly and then fails
every single export with "Encoder not found".

The failure mode these guard against is worse than a crash: the proxy
silently not building would just make scrubbing choppy again, with no
error anywhere.
"""

from __future__ import annotations

from pathlib import Path
import os

import pytest

from tapesift.models.clip import Clip
from tapesift.models.export_settings import SOURCE_QUALITY
from tapesift.services import ffmpeg_service, proxy_service

LGPL = frozenset({"libopenh264", "aac", "h264_nvenc"})
GPL = frozenset({"libx264", "libopenh264", "aac"})
UNKNOWN = frozenset()


@pytest.fixture
def encoders(monkeypatch):
    """Pretend a given FFmpeg build; the real one is not run here."""
    monkeypatch.setattr(ffmpeg_service, "_source_frame_rate", lambda *_: "30/1")
    def use(available):
        monkeypatch.setattr(ffmpeg_service, "available_encoders",
                            lambda path: available)
    return use


class TestEncoderChoice:
    def test_lgpl_build_falls_back_to_openh264(self, encoders):
        encoders(LGPL)
        assert ffmpeg_service.software_h264("ffmpeg") == "libopenh264"

    def test_gpl_build_keeps_x264(self, encoders):
        encoders(GPL)
        assert ffmpeg_service.software_h264("ffmpeg") == "libx264"

    def test_unreadable_encoder_list_does_not_downgrade(self, encoders):
        """Empty means "couldn't ask", not "doesn't have it" - downgrading
        a working machine because a probe failed would be a real regression."""
        encoders(UNKNOWN)
        assert ffmpeg_service.software_h264("ffmpeg") == "libx264"

    def test_no_path_is_safe(self):
        assert ffmpeg_service.software_h264("") == "libx264"


class TestExportCommand:
    def _cmd(self, tmp_path):
        return ffmpeg_service.build_clip_command(
            "ffmpeg", tmp_path / "in.mp4", tmp_path / "out.mp4",
            Clip(start_ms=0, end_ms=5000), SOURCE_QUALITY, accurate=True)

    def test_lgpl_export_uses_openh264(self, encoders, tmp_path):
        encoders(LGPL)
        cmd = self._cmd(tmp_path)
        assert "libopenh264" in cmd and "libx264" not in cmd

    def test_crf_becomes_a_bitrate(self, encoders, tmp_path):
        """openh264 has no CRF mode at all; passing -crf is silently useless."""
        encoders(LGPL)
        cmd = self._cmd(tmp_path)
        assert "-crf" not in cmd
        assert "-b:v" in cmd and cmd[cmd.index("-b:v") + 1].endswith("M")

    def test_x264_preset_is_dropped(self, encoders, tmp_path):
        """-preset veryfast is an x264 concept; openh264 rejects it."""
        encoders(LGPL)
        cmd = self._cmd(tmp_path)
        assert "-preset" not in cmd

    def test_gpl_export_is_unchanged(self, encoders, tmp_path):
        encoders(GPL)
        cmd = self._cmd(tmp_path)
        assert "libx264" in cmd and "-crf" in cmd


class TestProxyCommand:
    def test_lgpl_proxy_uses_openh264(self, encoders, tmp_path):
        encoders(LGPL)
        cmd = proxy_service.build_proxy_command(
            "ffmpeg", tmp_path / "in.mp4", tmp_path / "out.mp4")
        assert "libopenh264" in cmd
        assert "-tune" not in cmd and "-crf" not in cmd

    def test_hardware_encoder_still_wins(self, encoders, tmp_path):
        """NVENC doesn't care which software encoders the build has."""
        encoders(LGPL)
        cmd = proxy_service.build_proxy_command(
            "ffmpeg", tmp_path / "in.mp4", tmp_path / "out.mp4",
            hw_encoder="h264_nvenc")
        assert "h264_nvenc" in cmd and "libopenh264" not in cmd


class TestBundledLookup:
    def test_configured_path_beats_the_bundle(self, tmp_path):
        """A user who points at their own FFmpeg must keep getting it."""
        mine = tmp_path / "ffmpeg.exe"
        mine.write_bytes(b"")
        assert ffmpeg_service.find_executable("ffmpeg", str(mine)) == str(mine)

    def test_bundle_is_searched_before_path(self, tmp_path, monkeypatch):
        bundle = tmp_path / "ffmpeg"
        bundle.mkdir()
        exe = bundle / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        exe.write_bytes(b"")
        monkeypatch.setattr(ffmpeg_service, "bundled_dir", lambda: bundle)
        monkeypatch.setattr(ffmpeg_service.shutil, "which",
                            lambda name: r"C:\somewhere\else\ffmpeg.exe")
        assert ffmpeg_service.find_executable("ffmpeg") == str(exe)

    def test_missing_bundle_falls_through_to_path(self, monkeypatch):
        monkeypatch.setattr(ffmpeg_service, "bundled_dir", lambda: None)
        monkeypatch.setattr(ffmpeg_service.shutil, "which",
                            lambda name: r"C:\on\path\ffmpeg.exe")
        assert ffmpeg_service.find_executable("ffmpeg") == r"C:\on\path\ffmpeg.exe"


class TestDetectionIsNotAnOverride:
    """A remembered auto-detection must not outrank the bundled FFmpeg.

    This is how the first bundled build silently kept using the machine's
    own FFmpeg: an earlier run had auto-detected a path and saved it, and
    from then on it was indistinguishable from a user's deliberate choice.
    """

    def _settings(self, tmp_path, **kw):
        from tapesift.core.config import AppSettings
        s = AppSettings(**kw)
        s.save(tmp_path / "settings.json")
        return s

    def test_auto_detected_path_is_ignored_on_next_start(self, tmp_path,
                                                         monkeypatch):
        from tapesift.ui import ffmpeg_setup_dialog as dlg
        settings = self._settings(tmp_path, ffmpeg_path=r"C:\old\ffmpeg.exe",
                                  ffprobe_path=r"C:\old\ffprobe.exe",
                                  ffmpeg_path_manual=False)
        seen = []
        monkeypatch.setattr(dlg.ffmpeg_service, "find_executable",
                            lambda name, configured="": seen.append(configured)
                            or rf"C:\bundled\{name}.exe")
        monkeypatch.setattr(settings, "save", lambda *a, **k: None)
        assert dlg.ensure_ffmpeg(settings)
        assert seen == ["", ""], "stored path was treated as an override"
        assert settings.ffmpeg_path == r"C:\bundled\ffmpeg.exe"

    def test_manual_choice_is_respected(self, tmp_path, monkeypatch):
        from tapesift.ui import ffmpeg_setup_dialog as dlg
        settings = self._settings(tmp_path, ffmpeg_path=r"C:\mine\ffmpeg.exe",
                                  ffprobe_path=r"C:\mine\ffprobe.exe",
                                  ffmpeg_path_manual=True)
        seen = []
        monkeypatch.setattr(dlg.ffmpeg_service, "find_executable",
                            lambda name, configured="": seen.append(configured)
                            or configured)
        monkeypatch.setattr(settings, "save", lambda *a, **k: None)
        assert dlg.ensure_ffmpeg(settings)
        assert seen == [r"C:\mine\ffmpeg.exe", r"C:\mine\ffprobe.exe"]
