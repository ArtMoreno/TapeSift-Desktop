from pathlib import Path
import json
import threading
from types import SimpleNamespace

import pytest

from tapesift.services.proxy_service import (
    PROXY_GOP, build_proxy_command, cleanup_stale_proxies, find_ready_proxy,
    proxy_path_for,
)


class TestProxyPath:
    def test_stable_for_same_source(self, tmp_path: Path):
        src = tmp_path / "game.mp4"
        src.write_bytes(b"x" * 100)
        assert proxy_path_for(src, tmp_path) == proxy_path_for(src, tmp_path)

    def test_changes_when_source_changes(self, tmp_path: Path):
        src = tmp_path / "game.mp4"
        src.write_bytes(b"x" * 100)
        first = proxy_path_for(src, tmp_path)
        src.write_bytes(b"y" * 999)  # new size -> new fingerprint
        assert proxy_path_for(src, tmp_path) != first

    def test_find_ready_requires_existing_file(self, tmp_path: Path):
        src = tmp_path / "game.mp4"
        src.write_bytes(b"x")
        assert find_ready_proxy(src, tmp_path) is None
        target = proxy_path_for(src, tmp_path)
        target.parent.mkdir(parents=True)
        target.write_bytes(b"proxy")
        assert find_ready_proxy(src, tmp_path) == target

    def test_cleanup_removes_only_stale(self, tmp_path: Path):
        src = tmp_path / "game.mp4"
        src.write_bytes(b"x")
        current = proxy_path_for(src, tmp_path)
        current.parent.mkdir(parents=True)
        current.write_bytes(b"good")
        stale = current.parent / "game_0123456789.preview.mp4"
        stale.write_bytes(b"old")
        cleanup_stale_proxies(src, tmp_path)
        assert current.exists()
        assert not stale.exists()


class TestProxyCommand:
    SRC = Path("C:/videos/game.mp4")
    OUT = Path("C:/cache/game.preview.mp4")

    def test_cpu_command(self):
        cmd = build_proxy_command("ffmpeg", self.SRC, self.OUT)
        assert "libx264" in cmd
        assert "-g" in cmd and cmd[cmd.index("-g") + 1] == str(PROXY_GOP)
        assert "-bf" in cmd and cmd[cmd.index("-bf") + 1] == "0"
        assert any("scale=" in a for a in cmd)
        assert "+faststart" in cmd

    def test_nvenc_command(self):
        cmd = build_proxy_command("ffmpeg", self.SRC, self.OUT, "h264_nvenc")
        assert "h264_nvenc" in cmd
        assert "libx264" not in cmd
        assert cmd[cmd.index("-g") + 1] == str(PROXY_GOP)

    def test_progress_reporting_enabled(self):
        cmd = build_proxy_command("ffmpeg", self.SRC, self.OUT)
        assert "-progress" in cmd and "pipe:1" in cmd

    def test_preserves_source_frame_timing_and_selects_verified_stream(self):
        cmd = build_proxy_command("ffmpeg", self.SRC, self.OUT)
        assert cmd[cmd.index("-fps_mode") + 1] == "passthrough"
        assert [cmd[i + 1] for i, value in enumerate(cmd) if value == "-map"] == ["0:v:0", "0:a:0?"]

    def test_threads_capped_for_decode_and_encode(self):
        # Uncapped FFmpeg grabs every core and starves the preview decoder.
        cmd = build_proxy_command("ffmpeg", self.SRC, self.OUT)
        thread_flags = [i for i, a in enumerate(cmd) if a == "-threads"]
        assert len(thread_flags) == 2, "need both decode and encode caps"
        input_index = cmd.index("-i")
        assert thread_flags[0] < input_index   # decode threads
        assert thread_flags[1] > input_index   # encode threads
        assert all(cmd[i + 1] == "2" for i in thread_flags)


class TestBackgroundPriority:
    def test_runner_defaults_to_normal_priority(self):
        from tapesift.services.export_service import FFmpegRunner
        assert FFmpegRunner().low_priority is False        # user exports
        assert FFmpegRunner(low_priority=True).low_priority is True

    def test_proxy_worker_runs_low_priority(self, tmp_path: Path):
        from tapesift.workers.proxy_worker import ProxyWorker
        worker = ProxyWorker("ffmpeg", tmp_path / "a.mp4", tmp_path, 1000)
        assert worker.runner.low_priority is True

    def test_auto_build_defaults_on_but_preserves_saved_opt_out(self, tmp_path):
        from tapesift.core.config import AppSettings
        assert AppSettings().scrub_proxy_enabled is True
        target = tmp_path / "settings.json"
        AppSettings(scrub_proxy_enabled=False).save(target)
        assert AppSettings.load(target).scrub_proxy_enabled is False


class TestSettingsEncoding:
    def test_bom_does_not_reset_settings(self, tmp_path: Path):
        """A BOM used to make the whole settings file silently fall back."""
        from tapesift.core.config import AppSettings
        settings = AppSettings()
        settings.volume = 42
        target = tmp_path / "settings.json"
        settings.save(target)
        raw = target.read_bytes()
        target.write_bytes(b"\xef\xbb\xbf" + raw)  # prepend a UTF-8 BOM
        assert AppSettings.load(target).volume == 42


def test_timing_probe_sorts_b_frames_preserves_duplicates_and_vfr(monkeypatch):
    from tapesift.services import proxy_service as service
    data = {"format": {"start_time": "10"}, "packets": [
        {"pts_time": "10.8"}, {"pts_time": "10"}, {"pts_time": "10.1"},
        {"pts_time": "10.1"}, {"pts_time": "9", "flags": "_D"}]}
    monkeypatch.setattr(service.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=json.dumps(data)))
    assert service.video_timestamps("ffmpeg", Path("film.mp4")) == pytest.approx([0, .1, .1, .8])
    assert service.verify_timing([0, .1, .8], [.0001, .1002, .8001]) == ""


@pytest.mark.parametrize("packets", ([], [{}], [{"pts_time": "NaN"}]))
def test_timing_probe_rejects_missing_or_invalid_timestamps(monkeypatch, packets):
    from tapesift.services import proxy_service as service
    monkeypatch.setattr(service.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=json.dumps({"packets": packets})))
    with pytest.raises((ValueError, KeyError)):
        service.video_timestamps("ffmpeg", Path("film.mp4"))


def test_timing_check_rejects_missing_frames_and_equal_count_retiming():
    from tapesift.services.proxy_service import verify_timing
    source = [index / 60 for index in range(60)]
    assert "samples" in verify_timing(source, source[:8] + source[22:])
    changed = list(source)
    changed[30] += .250
    assert "timestamp" in verify_timing(source, changed)


@pytest.mark.parametrize("outcome", ("valid", "missing", "cancel", "cancel_failed_validation"))
def test_proxy_promotion_preserves_old_files_on_failure_or_cancel(tmp_path, monkeypatch, outcome):
    from tapesift.services import proxy_service as service
    from tapesift.workers.proxy_worker import ProxyWorker
    source = tmp_path / "film.mp4"
    source.write_bytes(b"source")
    target = service.proxy_path_for(source, tmp_path)
    target.parent.mkdir()
    target.write_bytes(b"old preview")
    stale = target.parent / "film_older.preview.mp4"
    stale.write_bytes(b"previous source preview")
    worker = ProxyWorker("ffmpeg", source, tmp_path, 1000, ["h264_nvenc"] if outcome == "cancel_failed_validation" else [])
    calls, ready = [], []
    cancelled = threading.Event()
    def encode(cmd, *_args):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"new preview")
        if outcome == "cancel":
            cancelled.set()
    worker.runner = SimpleNamespace(run=encode, cancel_event=cancelled)
    monkeypatch.setattr(service, "build_proxy_command", lambda _f, _s, dst, _e: [str(dst)])
    monkeypatch.setattr(service, "verify_playable", lambda *_: "")
    def timestamps(_ffmpeg, path):
        if path == source:
            return [0, .5]
        if outcome == "cancel_failed_validation":
            cancelled.set()
            raise ValueError("interrupted probe")
        return [0] if outcome == "missing" else [0, .5]
    monkeypatch.setattr(service, "video_timestamps", timestamps)
    worker.proxy_ready.connect(lambda *args: ready.append(args))
    worker.run()
    assert len(calls) == 1
    assert not target.with_suffix(".part.mp4").exists()
    if outcome == "valid":
        assert target.read_bytes() == b"new preview"
        assert len(ready) == 1 and not stale.exists()
    else:
        assert target.read_bytes() == b"old preview"
        assert stale.read_bytes() == b"previous source preview"
        assert not ready
