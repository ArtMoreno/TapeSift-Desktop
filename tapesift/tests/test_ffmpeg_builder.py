from pathlib import Path

import pytest

from tapesift.models.clip import Clip
from tapesift.models.export_settings import (
    FAST_COPY, SOCIAL_1080P, SOURCE_QUALITY, VERTICAL_9_16,
)
from tapesift.services.ffmpeg_service import (
    build_clip_command, build_composited_command, build_concat_command,
    build_thumbnail_command, ms_to_ffmpeg_time, parse_progress_line,
)

SRC = Path("C:/videos/My Long Video.mp4")
OUT = Path("C:/out/003_Final-reveal.mp4")


def make_clip() -> Clip:
    return Clip(start_ms=1_120_000, end_ms=1_135_000)  # 18:40 → 18:55


class TestTimeConversion:
    def test_ms_to_seconds(self):
        assert ms_to_ffmpeg_time(1_120_000) == "1120.000"
        assert ms_to_ffmpeg_time(1_500) == "1.500"


class TestClipCommand:
    @pytest.fixture(autouse=True)
    def source_cadence(self, monkeypatch):
        from tapesift.services import ffmpeg_service
        monkeypatch.setattr(ffmpeg_service, "_source_frame_rate", lambda *_: "30000/1001")

    def test_accurate_cut_reencodes(self):
        cmd = build_clip_command("ffmpeg", SRC, OUT, make_clip(), SOURCE_QUALITY, accurate=True)
        assert cmd[0] == "ffmpeg"
        assert "-ss" in cmd and cmd[cmd.index("-ss") + 1] == "1120.000"
        assert "-t" in cmd and cmd[cmd.index("-t") + 1] == "15.000"
        codec = cmd[cmd.index("-c:v") + 1]
        assert codec in {"libx264", "libopenh264"}
        if codec == "libx264":
            assert "-crf" in cmd and cmd[cmd.index("-crf") + 1] == "18"
        else:
            assert "-crf" not in cmd
            assert "-b:v" in cmd and cmd[cmd.index("-b:v") + 1] == "12M"
        assert cmd[cmd.index("-r") + 1] == "30000/1001"
        assert cmd[cmd.index("-vf") + 1] == "setpts=PTS-STARTPTS"
        assert "aac" in cmd
        assert "yuv420p" in cmd
        assert str(OUT) in cmd

    def test_fast_copy_uses_stream_copy(self):
        cmd = build_clip_command("ffmpeg", SRC, OUT, make_clip(), FAST_COPY)
        assert "-c" in cmd and cmd[cmd.index("-c") + 1] == "copy"
        assert "libx264" not in cmd

    def test_fast_mode_overrides_preset(self):
        cmd = build_clip_command("ffmpeg", SRC, OUT, make_clip(), SOURCE_QUALITY, accurate=False)
        assert "copy" in cmd

    def test_social_preset_scales(self):
        cmd = build_clip_command("ffmpeg", SRC, OUT, make_clip(), SOCIAL_1080P)
        vf = cmd[cmd.index("-vf") + 1]
        assert vf.startswith("setpts=PTS-STARTPTS,")
        assert "1920" in vf and "1080" in vf

    def test_vertical_preset_crops(self):
        cmd = build_clip_command("ffmpeg", SRC, OUT, make_clip(), VERTICAL_9_16)
        vf = cmd[cmd.index("-vf") + 1]
        assert "crop=1080:1920" in vf

    def test_paths_with_spaces_stay_single_args(self):
        cmd = build_clip_command("ffmpeg", SRC, OUT, make_clip(), SOURCE_QUALITY)
        assert str(SRC) in cmd  # one list element, not split

    def test_progress_reporting_enabled(self):
        cmd = build_clip_command("ffmpeg", SRC, OUT, make_clip(), SOURCE_QUALITY)
        assert "-progress" in cmd


class TestConcatCommand:
    def test_concat(self):
        cmd = build_concat_command("ffmpeg", Path("list.txt"), Path("reel.mp4"))
        assert "concat" in cmd
        assert "copy" in cmd


class TestThumbnailCommand:
    def test_thumbnail(self):
        cmd = build_thumbnail_command("ffmpeg", SRC, Path("thumb.jpg"), 5_000)
        assert "-frames:v" in cmd
        assert cmd[cmd.index("-ss") + 1] == "5.000"


class TestProgressParsing:
    def test_out_time(self):
        assert parse_progress_line("out_time_us=7500000") == 7_500_000

    def test_non_progress_line(self):
        assert parse_progress_line("speed=3.1x") is None


class TestCompositedCommand:
    def test_canvas_clock_and_voiceover_are_owned_by_compositor(self):
        cmd = build_composited_command(
            "ffmpeg",
            Path("voice.wav"),
            Path("signature.mp4"),
            width=1920,
            height=1080,
            frame_rate=30,
            preset=SOCIAL_1080P,
        )
        assert cmd[:4] == ["ffmpeg", "-hide_banner", "-y", "-f"]
        assert cmd[cmd.index("-pixel_format") + 1] == "rgba"
        assert cmd[cmd.index("-video_size") + 1] == "1920x1080"
        assert cmd[cmd.index("-framerate") + 1] == "30"
        assert [
            cmd[index + 1]
            for index, value in enumerate(cmd)
            if value == "-i"
        ] == ["pipe:0", "voice.wav"]
        assert "scale=" not in " ".join(cmd)
        assert "crop=" not in " ".join(cmd)
        assert "-shortest" in cmd
        assert cmd[-1] == "signature.mp4"

    def test_rejects_stream_copy_and_legacy_vertical_transform(self):
        with pytest.raises(ValueError, match="stream copy"):
            build_composited_command(
                "ffmpeg", Path("voice.wav"), Path("out.mp4"),
                width=1920, height=1080, frame_rate=30,
                preset=FAST_COPY,
            )
        with pytest.raises(ValueError, match="legacy canvas"):
            build_composited_command(
                "ffmpeg", Path("voice.wav"), Path("out.mp4"),
                width=1080, height=1920, frame_rate=30,
                preset=VERTICAL_9_16,
            )


class TestAccurateSourceClock:
    def test_irregular_cadence_keeps_existing_timing(self, monkeypatch):
        from tapesift.services import ffmpeg_service
        monkeypatch.setattr(ffmpeg_service, "_source_frame_rate", lambda *_: "")
        cmd = build_clip_command("ffmpeg", SRC, OUT, make_clip(), SOURCE_QUALITY)
        assert "-r" not in cmd and "-vf" not in cmd

    def test_copy_and_intentional_conversion_do_not_probe_source_rate(self, monkeypatch):
        from dataclasses import replace
        from tapesift.services import ffmpeg_service
        def forbidden(*_):
            raise AssertionError("Unexpected source rate probe")
        monkeypatch.setattr(ffmpeg_service, "_source_frame_rate", forbidden)
        for preset, accurate in ((FAST_COPY, True), (SOURCE_QUALITY, False)):
            cmd = build_clip_command("ffmpeg", SRC, OUT, make_clip(), preset, accurate)
            assert "-r" not in cmd and "-vf" not in cmd
        preset = replace(SOURCE_QUALITY, frame_rate=24)
        cmd = build_clip_command("ffmpeg", SRC, OUT, make_clip(), preset)
        assert cmd.count("-r") == 1 and cmd[cmd.index("-r") + 1] == "24"
        assert "setpts=PTS-STARTPTS" in cmd

    def test_probe_preserves_rational_and_invalidates_replaced_source(self, tmp_path, monkeypatch):
        import json
        from types import SimpleNamespace
        from tapesift.services import ffmpeg_service as ff
        source = tmp_path / "source.mp4"; source.write_bytes(b"source")
        encoder = tmp_path / "ffmpeg.exe"; probe = tmp_path / ("ffprobe.exe" if ff.os.name == "nt" else "ffprobe"); probe.touch()
        calls = []
        def run(cmd, **kwargs):
            calls.append(cmd)
            return SimpleNamespace(stdout=json.dumps({"streams":[{
                "avg_frame_rate":"30000/1001", "r_frame_rate":"30000/1001"}]}))
        monkeypatch.setattr(ff.subprocess, "run", run)
        ff._probe_constant_frame_rate.cache_clear()
        assert ff._source_frame_rate(str(encoder), source) == "30000/1001"
        assert ff._source_frame_rate(str(encoder), source) == "30000/1001"
        assert len(calls) == 1 and calls[0][0] == str(probe)
        source.write_bytes(b"replacement")
        assert ff._source_frame_rate(str(encoder), source) == "30000/1001"
        assert len(calls) == 2

    @pytest.mark.parametrize("rates", [None, {}, {"avg_frame_rate":"0/0","r_frame_rate":"30/1"},
        {"avg_frame_rate":"-1/1","r_frame_rate":"30/1"},
        {"avg_frame_rate":"bad","r_frame_rate":"30/1"},
        {"avg_frame_rate":30,"r_frame_rate":"30/1"}])
    def test_unreadable_cadence_fails_without_caching(self, tmp_path, monkeypatch, rates):
        import json
        from types import SimpleNamespace
        from tapesift.core.exceptions import ExportError
        from tapesift.services import ffmpeg_service as ff
        source = tmp_path / "source.mp4"; source.touch()
        monkeypatch.setattr(ff, "find_executable", lambda *_: "ffprobe")
        monkeypatch.setattr(ff.subprocess, "run", lambda *a, **k:
            SimpleNamespace(stdout=json.dumps({"streams": [] if rates is None else [rates]})))
        ff._probe_constant_frame_rate.cache_clear()
        with pytest.raises(ExportError): ff._source_frame_rate("ffmpeg", source)
        assert ff._probe_constant_frame_rate.cache_info().currsize == 0

    def test_disagreeing_rates_are_not_normalized(self, tmp_path, monkeypatch):
        from types import SimpleNamespace
        from tapesift.services import ffmpeg_service as ff
        source = tmp_path / "source.mp4"; source.touch()
        monkeypatch.setattr(ff, "find_executable", lambda *_: "ffprobe")
        monkeypatch.setattr(ff.subprocess, "run", lambda *a, **k: SimpleNamespace(
            stdout='{"streams":[{"avg_frame_rate":"29/1","r_frame_rate":"30/1"}]}'))
        ff._probe_constant_frame_rate.cache_clear()
        assert ff._source_frame_rate("ffmpeg", source) == ""


    @pytest.mark.parametrize("failure", ["timeout", "exit"])
    def test_probe_process_failure_is_a_clear_export_error(self, tmp_path, monkeypatch, failure):
        import subprocess
        from tapesift.core.exceptions import ExportError
        from tapesift.services import ffmpeg_service as ff
        source = tmp_path / "source.mp4"; source.touch()
        monkeypatch.setattr(ff, "find_executable", lambda *_: "ffprobe")
        def run(*args, **kwargs):
            if failure == "timeout":
                raise subprocess.TimeoutExpired("ffprobe", 20)
            raise subprocess.CalledProcessError(1, "ffprobe")
        monkeypatch.setattr(ff.subprocess, "run", run)
        ff._probe_constant_frame_rate.cache_clear()
        with pytest.raises(ExportError, match="Could not determine"):
            ff._source_frame_rate("ffmpeg", source)
        assert ff._probe_constant_frame_rate.cache_info().currsize == 0
