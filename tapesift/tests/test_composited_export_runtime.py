from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import threading
import time
from fractions import Fraction

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tapesift.core.exceptions import (  # noqa: E402
    ExportCancelledError,
    ExportError,
)
from tapesift.models.export_settings import SOCIAL_1080P  # noqa: E402
from tapesift.models.export_package import ExportStyle  # noqa: E402
from tapesift.services import ffmpeg_service  # noqa: E402
from tapesift.services import composited_export_service as runtime_module  # noqa: E402
from tapesift.services.composited_export_service import (  # noqa: E402
    CompositedExportRuntime,
    RandomAccessFFmpegFrameLoader,
    export_composited_sequence,
)
from tapesift.services.preview_compositor import DecodedSourceFrame  # noqa: E402
from tapesift.tests.test_presentation_sequence_renderer import (  # noqa: E402
    _fixture,
    _no_voiceover_fixture,
)


@pytest.fixture(scope="module", autouse=True)
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _bundled(name: str) -> str:
    value = ffmpeg_service.find_executable(name)
    if not value:
        pytest.skip(f"Bundled {name} is unavailable")
    return value


def _source_video(tmp_path: Path, *, duration_seconds: int = 1) -> Path:
    output = tmp_path / f"source-{duration_seconds}s.mkv"
    command = [
        _bundled("ffmpeg"),
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-f", "lavfi",
        "-i", f"color=c=#214C2F:s=32x18:r=30:d={duration_seconds}",
        "-c:v", "ffv1",
        str(output),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        creationflags=ffmpeg_service.CREATE_NO_WINDOW,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr.decode(
        "utf-8", errors="replace"
    )
    return output


def _vfr_source(tmp_path: Path) -> Path:
    images: list[Path] = []
    for name, color in (
        ("red", "#FF0000"),
        ("green", "#00FF00"),
        ("blue", "#0000FF"),
    ):
        image = QImage(32, 18, QImage.Format.Format_RGB32)
        image.fill(QColor(color))
        path = tmp_path / f"{name}.png"
        assert image.save(str(path), "PNG")
        images.append(path)
    concat = tmp_path / "vfr.txt"
    concat.write_text(
        "\n".join((
            f"file '{images[0].as_posix()}'",
            "duration 0.100",
            f"file '{images[1].as_posix()}'",
            "duration 0.400",
            f"file '{images[2].as_posix()}'",
            "duration 0.100",
            f"file '{images[2].as_posix()}'",
        )),
        encoding="utf-8",
    )
    output = tmp_path / "vfr.mkv"
    completed = subprocess.run(
        [
            _bundled("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat),
            "-fps_mode", "vfr", "-c:v", "ffv1", str(output),
        ],
        capture_output=True,
        creationflags=ffmpeg_service.CREATE_NO_WINDOW,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr.decode(
        "utf-8", errors="replace"
    )
    return output


def _shifted_vfr_source(tmp_path: Path) -> Path:
    source = _vfr_source(tmp_path)
    output = tmp_path / "vfr-nonzero-start.mkv"
    completed = subprocess.run(
        [
            _bundled("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
            "-copyts", "-i", str(source),
            "-vf", "setpts=PTS+5/TB",
            "-fps_mode", "vfr", "-c:v", "ffv1", str(output),
        ],
        capture_output=True,
        creationflags=ffmpeg_service.CREATE_NO_WINDOW,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr.decode(
        "utf-8", errors="replace"
    )
    return output


def _b_frame_source(tmp_path: Path) -> Path:
    output = tmp_path / "b-frames.mkv"
    completed = subprocess.run(
        [
            _bundled("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=s=64x36:r=30:d=2",
            "-c:v", "mpeg4", "-bf", "2", "-g", "30", str(output),
        ],
        capture_output=True,
        creationflags=ffmpeg_service.CREATE_NO_WINDOW,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr.decode(
        "utf-8", errors="replace"
    )
    return output


def _pixel(frame: DecodedSourceFrame) -> tuple[int, int, int]:
    image = QImage(
        frame.rgba,
        frame.width,
        frame.height,
        frame.width * 4,
        QImage.Format.Format_RGBA8888,
    )
    assert not image.isNull()
    color = image.pixelColor(0, 0)
    return color.red(), color.green(), color.blue()


def test_random_access_loader_returns_raw_frame_and_reuses_paused_frame(
        tmp_path):
    source = _source_video(tmp_path)
    loader = RandomAccessFFmpegFrameLoader(
        _bundled("ffmpeg"), source, cache_entries=2
    )
    first = loader.load(100)
    assert isinstance(first, DecodedSourceFrame)
    assert loader.load(100) is first


def test_vfr_loader_floors_to_displayed_pts_and_batches_exact_frames(tmp_path):
    source = _vfr_source(tmp_path)
    requests = (0, 50, 99, 100, 250, 499, 500)
    loader = RandomAccessFFmpegFrameLoader(
        _bundled("ffmpeg"), source, cache_entries=2
    )
    loader.prepare_requests(requests)
    colors = {position: _pixel(loader.load(position)) for position in requests}
    assert colors == {
        0: (255, 0, 0),
        50: (255, 0, 0),
        99: (255, 0, 0),
        100: (255, 0, 0),
        250: (0, 255, 0),
        499: (0, 255, 0),
        500: (0, 255, 0),
    }
    assert loader.decoder_process_count == 1


def test_vfr_reverse_span_decodes_forward_once_and_serves_exact_floor(
        tmp_path):
    source = _vfr_source(tmp_path)
    requests = (500, 250, 100, 50, 0)
    loader = RandomAccessFFmpegFrameLoader(_bundled("ffmpeg"), source)
    loader.prepare_requests(requests)
    assert [_pixel(loader.load(position)) for position in requests] == [
        (0, 255, 0),
        (0, 255, 0),
        (255, 0, 0),
        (255, 0, 0),
        (255, 0, 0),
    ]
    assert loader.decoder_process_count == 1
    assert 0 < loader.peak_reverse_cache_bytes \
        <= loader._REVERSE_MEMORY_BYTES  # noqa: SLF001


def test_nonzero_first_pts_keeps_relative_lookup_and_absolute_select(tmp_path):
    source = _shifted_vfr_source(tmp_path)
    loader = RandomAccessFFmpegFrameLoader(_bundled("ffmpeg"), source)
    requests = (0, 50, 250, 500)
    loader.prepare_requests(requests)
    assert loader._index.frames[0].pts > 0  # noqa: SLF001
    assert [_pixel(loader.load(value)) for value in requests] == [
        (255, 0, 0),
        (255, 0, 0),
        (0, 255, 0),
        (0, 255, 0),
    ]


def test_pts_probe_normalizes_decode_order_and_duplicate_timestamps(
        tmp_path, monkeypatch):
    source = tmp_path / "source.mkv"
    source.write_bytes(b"source")
    payload = json.dumps({
        "packets": [
            {"pts": value}
            for value in (-20, -10, 0, 40, 20, 20)
        ],
        "streams": [{
            "time_base": "1/1000", "start_pts": 0,
            "width": 32, "height": 18,
        }],
    }).encode("utf-8")

    class FakeProbe:
        stdin = None
        returncode = 0

        def __init__(self, *_args, **_kwargs):
            pass

        def communicate(self, timeout=None):
            return payload, b""

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(runtime_module.subprocess, "Popen", FakeProbe)
    loader = RandomAccessFFmpegFrameLoader(
        _bundled("ffmpeg"), source, ffprobe_path="ffprobe"
    )
    index = loader._ensure_index()  # noqa: SLF001
    assert [frame.pts for frame in index.frames] == [0, 20, 40]
    assert index.multiplicities == (1, 2, 1)
    assert index.frame_index_at_ms(25) == 1


def test_packet_pts_index_matches_decoded_presentation_order_for_b_frames(
        tmp_path):
    source = _b_frame_source(tmp_path)
    loader = RandomAccessFFmpegFrameLoader(_bundled("ffmpeg"), source)
    index = loader._ensure_index()  # noqa: SLF001
    completed = subprocess.run(
        [
            _bundled("ffprobe"), "-v", "error", "-select_streams", "v:0",
            "-show_entries", "frame=best_effort_timestamp",
            "-show_frames", "-of", "json", str(source),
        ],
        capture_output=True,
        check=True,
        creationflags=ffmpeg_service.CREATE_NO_WINDOW,
        timeout=30,
    )
    decoded = json.loads(completed.stdout)["frames"]
    decoded_pts = tuple(sorted({
        int(frame["best_effort_timestamp"]) for frame in decoded
    }))
    assert tuple(frame.pts for frame in index.frames) == decoded_pts
    assert len(index.frames) == 60


def test_monotonic_second_uses_one_bounded_decoder_process(tmp_path):
    source = _source_video(tmp_path)
    requests = tuple(range(0, 991, 33))
    loader = RandomAccessFFmpegFrameLoader(
        _bundled("ffmpeg"), source, cache_entries=2
    )
    loader.prepare_requests(requests)
    for position in requests:
        assert isinstance(loader.load(position), DecodedSourceFrame)
    assert loader.decoder_process_count == 1


def test_long_monotonic_shape_uses_bounded_windows_not_per_frame_processes(
        tmp_path):
    source = _source_video(tmp_path, duration_seconds=10)
    requests = tuple(index * 33 for index in range(300))
    loader = RandomAccessFFmpegFrameLoader(
        _bundled("ffmpeg"), source, cache_entries=2
    )
    loader.prepare_requests(requests)
    for position in requests:
        loader.load(position)
    assert loader.decoder_process_count == 1


def test_eight_x_forward_steps_stream_skipped_indexes_in_one_process(tmp_path):
    source = _source_video(tmp_path, duration_seconds=10)
    # At 30 fps, 264 ms advances roughly eight delivered source frames for
    # each output frame. The loader streams and discards those intervening
    # frames without opening one decoder per requested frame.
    requests = tuple(range(0, 9_505, 264))
    loader = RandomAccessFFmpegFrameLoader(_bundled("ffmpeg"), source)
    loader.prepare_requests(requests)
    planned = loader._planned_indexes  # noqa: SLF001
    assert len(planned) == len(requests)
    assert all(right > left for left, right in zip(planned, planned[1:]))
    assert max(right - left for left, right in zip(planned, planned[1:])) >= 8
    for position in requests:
        assert isinstance(loader.load(position), DecodedSourceFrame)
    assert loader.decoder_process_count == 1


def test_hard_seek_starts_one_new_persistent_decoder_segment(tmp_path):
    source = _source_video(tmp_path, duration_seconds=10)
    requests = (0, 33, 66, 5_000, 5_033)
    loader = RandomAccessFFmpegFrameLoader(_bundled("ffmpeg"), source)
    loader.prepare_requests(requests)
    for position in requests:
        loader.load(position)
    assert loader.decoder_process_count == 2


def test_cancel_during_popen_assignment_terminates_published_decoder(
        tmp_path, monkeypatch):
    source = tmp_path / "source.mkv"
    source.write_bytes(b"source")
    construction_started = threading.Event()
    allow_constructor_return = threading.Event()
    signals: list[str] = []

    class FakeProcess:
        stdin = None

        def __init__(self, *_args, **_kwargs):
            self.returncode = None
            construction_started.set()
            allow_constructor_return.wait(2)

        def communicate(self, timeout=None):
            self.returncode = 0
            return b"", b""

        def poll(self):
            return self.returncode

        def terminate(self):
            signals.append("terminate")
            self.returncode = -15

        def kill(self):
            signals.append("kill")
            self.returncode = -9

        def wait(self, timeout=None):
            return self.returncode

    loader = RandomAccessFFmpegFrameLoader(
        _bundled("ffmpeg"), source
    )
    loader._index = runtime_module._VideoPtsIndex(  # noqa: SLF001
        time_base=Fraction(1, 1_000),
        frames=(runtime_module._IndexedFrame(0, 0),),  # noqa: SLF001
        relative_times_us=(0,),
        multiplicities=(1,),
        width=32,
        height=18,
    )
    loader.prepare_requests((0,))
    result: list[type[BaseException]] = []

    def load() -> None:
        try:
            loader.load(0)
        except BaseException as exc:  # proof captures the cancellation type
            result.append(type(exc))

    monkeypatch.setattr(runtime_module.subprocess, "Popen", FakeProcess)
    thread = threading.Thread(target=load)
    thread.start()
    assert construction_started.wait(1)
    started = time.monotonic()
    loader.cancel()
    allow_constructor_return.set()
    thread.join(1)
    assert not thread.is_alive()
    assert time.monotonic() - started < 0.5
    assert result == [ExportCancelledError]
    assert signals == ["terminate"]


def test_cancel_interrupts_blocked_raw_frame_read_as_cancelled(
        tmp_path, monkeypatch):
    source = tmp_path / "source.mkv"
    source.write_bytes(b"source")
    entered_read = threading.Event()
    process_stopped = threading.Event()
    signals: list[str] = []

    class BlockingStdout:
        closed = False

        def read(self, _byte_count):
            entered_read.set()
            process_stopped.wait(2)
            raise OSError("decoder pipe closed during cancellation")

    class EmptyStderr:
        def readline(self):
            return b""

    class BlockingDecoder:
        stdin = None

        def __init__(self, *_args, **_kwargs):
            self.stdout = BlockingStdout()
            self.stderr = EmptyStderr()
            self.returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            signals.append("terminate")
            self.returncode = -15
            process_stopped.set()

        def kill(self):
            signals.append("kill")
            self.returncode = -9
            process_stopped.set()

        def wait(self, timeout=None):
            if self.returncode is None:
                if not process_stopped.wait(timeout):
                    raise subprocess.TimeoutExpired("ffmpeg", timeout)
                self.returncode = -15
            return self.returncode

    loader = RandomAccessFFmpegFrameLoader(_bundled("ffmpeg"), source)
    loader._index = runtime_module._VideoPtsIndex(  # noqa: SLF001
        time_base=Fraction(1, 1_000),
        frames=(runtime_module._IndexedFrame(0, 0),),  # noqa: SLF001
        relative_times_us=(0,),
        multiplicities=(1,),
        width=32,
        height=18,
    )
    loader.prepare_requests((0,))
    monkeypatch.setattr(runtime_module.subprocess, "Popen", BlockingDecoder)
    errors: list[type[BaseException]] = []

    def load() -> None:
        try:
            loader.load(0)
        except BaseException as exc:  # proof captures the cancellation type
            errors.append(exc)

    thread = threading.Thread(target=load)
    thread.start()
    assert entered_read.wait(1)
    loader.cancel()
    thread.join(1)
    assert not thread.is_alive()
    assert [type(e) for e in errors] == [ExportCancelledError]
    assert signals == ["terminate"]


def test_bounded_termination_escalates_from_terminate_to_kill():
    signals: list[object] = []

    class HungProcess:
        stdin = None

        def __init__(self):
            self.killed = False

        def poll(self):
            return -9 if self.killed else None

        def terminate(self):
            signals.append("terminate")

        def kill(self):
            signals.append("kill")
            self.killed = True

        def wait(self, timeout=None):
            signals.append(("wait", timeout))
            if not self.killed:
                raise subprocess.TimeoutExpired("ffmpeg", timeout)
            return -9

    runtime_module._terminate_bounded(HungProcess())  # noqa: SLF001
    assert signals == [
        "terminate",
        ("wait", runtime_module._TERMINATE_TIMEOUT_SECONDS),  # noqa: SLF001
        "kill",
        ("wait", runtime_module._KILL_TIMEOUT_SECONDS),  # noqa: SLF001
    ]


def test_owner_cancel_stays_bounded_when_child_survives_terminate_and_kill(
        monkeypatch):
    signals: list[object] = []
    close_called = threading.Event()
    release_close = threading.Event()

    class LockedStdin:
        closed = False

        def close(self):
            close_called.set()
            release_close.wait(5)

    class StubbornProcess:
        def __init__(self, *_args, **_kwargs):
            self.stdin = LockedStdin()
            self.stdout = None
            self.stderr = None

        def poll(self):
            return None

        def terminate(self):
            signals.append("terminate")

        def kill(self):
            signals.append("kill")

        def wait(self, timeout=None):
            signals.append(("wait", timeout))
            raise subprocess.TimeoutExpired("ffmpeg", timeout)

    monkeypatch.setattr(runtime_module.subprocess, "Popen", StubbornProcess)
    monkeypatch.setattr(runtime_module, "_TERMINATE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(runtime_module, "_KILL_TIMEOUT_SECONDS", 0.01)
    runtime = CompositedExportRuntime()
    runtime._owner.start(["fake-encoder"])  # noqa: SLF001
    cancel_thread = threading.Thread(target=runtime.cancel, daemon=True)
    try:
        cancel_thread.start()
        cancel_thread.join(0.5)
        assert not cancel_thread.is_alive()
        assert signals == [
            "terminate", ("wait", 0.01), "kill", ("wait", 0.01),
        ]
        assert not close_called.is_set()
    finally:
        release_close.set()


def test_real_bundled_ffmpeg_signature_export_has_video_and_voiceover(tmp_path):
    source = _source_video(tmp_path)
    plan, inputs = _fixture()
    output = tmp_path / "signature.mp4"
    progress: list[float] = []
    command = export_composited_sequence(
        _bundled("ffmpeg"),
        source,
        output,
        plan,
        inputs,
        SOCIAL_1080P,
        on_progress=progress.append,
    )
    assert output.is_file() and output.stat().st_size > 0
    assert "rawvideo" in command and "voiceover.wav" in command
    assert progress[-1] == 100.0
    assert progress == sorted(progress)

    probe = subprocess.run(
        [
            _bundled("ffprobe"),
            "-v", "error",
            "-show_streams",
            "-of", "json",
            str(output),
        ],
        capture_output=True,
        creationflags=ffmpeg_service.CREATE_NO_WINDOW,
        timeout=30,
        check=True,
    )
    streams = json.loads(probe.stdout)["streams"]
    video = next(stream for stream in streams if stream["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1920, 1080)
    assert any(stream["codec_type"] == "audio" for stream in streams)


def test_real_bundled_ffmpeg_vertical_export_preserves_compositor_canvas(
        tmp_path):
    source = _source_video(tmp_path)
    plan, inputs = _fixture(style=ExportStyle.VERTICAL)
    output = tmp_path / "vertical.mp4"
    export_composited_sequence(
        _bundled("ffmpeg"), source, output, plan, inputs, SOCIAL_1080P
    )
    completed = subprocess.run(
        [
            _bundled("ffprobe"), "-v", "error", "-show_streams",
            "-show_format", "-of", "json", str(output),
        ],
        capture_output=True,
        creationflags=ffmpeg_service.CREATE_NO_WINDOW,
        timeout=30,
        check=True,
    )
    metadata = json.loads(completed.stdout)
    video = next(
        stream for stream in metadata["streams"]
        if stream["codec_type"] == "video"
    )
    audio = next(
        stream for stream in metadata["streams"]
        if stream["codec_type"] == "audio"
    )
    assert (video["width"], video["height"]) == (1080, 1920)
    assert video["codec_name"] == "h264"
    assert audio["codec_name"] == "aac"
    assert abs(float(video["duration"]) - float(audio["duration"])) <= 1 / 30


def test_cancelled_export_never_publishes_partial_output(tmp_path):
    source = _source_video(tmp_path)
    plan, inputs = _fixture()
    output = tmp_path / "cancelled.mp4"
    event = threading.Event()
    event.set()
    with pytest.raises(ExportCancelledError):
        export_composited_sequence(
            _bundled("ffmpeg"), source, output, plan, inputs,
            SOCIAL_1080P, cancel_event=event,
        )
    assert not output.exists()
    assert not tuple(tmp_path.glob("*.part.mp4"))


def test_midstream_cancel_interrupts_encoder_and_removes_unique_part(tmp_path):
    source = _source_video(tmp_path)
    plan, inputs = _fixture()
    output = tmp_path / "cancelled-midstream.mp4"
    event = threading.Event()

    def cancel_after_first_frame(progress: float) -> None:
        if progress >= 50.0:
            event.set()

    with pytest.raises(ExportCancelledError):
        export_composited_sequence(
            _bundled("ffmpeg"), source, output, plan, inputs,
            SOCIAL_1080P, cancel_event=event,
            on_progress=cancel_after_first_frame,
        )
    assert not output.exists()
    assert not tuple(tmp_path.glob("*.part.mp4"))


def test_cancel_signals_process_before_closing_blocked_encoder_stdin(
        tmp_path, monkeypatch):
    source = tmp_path / "source.mkv"
    source.write_bytes(b"source")
    plan, inputs = _fixture()
    output = tmp_path / "cancelled-blocked-write.mp4"
    write_entered = threading.Event()
    process_stopped = threading.Event()
    signals: list[str] = []

    class LockedWriter:
        def __init__(self):
            self.closed = False
            self._lock = threading.Lock()

        def write(self, _payload):
            with self._lock:
                write_entered.set()
                process_stopped.wait(5)
                raise BrokenPipeError("encoder stopped")

        def close(self):
            # This is the same lock held by write(). Closing before signalling
            # the child reproduces the cancellation deadlock.
            with self._lock:
                self.closed = True

    class EmptyPipe:
        def __init__(self):
            self.closed = False

        def readline(self):
            return b""

        def close(self):
            self.closed = True

    class BlockedEncoder:
        def __init__(self, command, **_kwargs):
            self.stdin = LockedWriter()
            self.stdout = EmptyPipe()
            self.stderr = EmptyPipe()
            self.returncode = None
            Path(command[-1]).write_bytes(b"unpublished partial")

        def poll(self):
            return self.returncode

        def terminate(self):
            signals.append("terminate")
            self.returncode = -15
            process_stopped.set()

        def kill(self):
            signals.append("kill")
            self.returncode = -9
            process_stopped.set()

        def wait(self, timeout=None):
            if self.returncode is None:
                if not process_stopped.wait(timeout):
                    raise subprocess.TimeoutExpired("ffmpeg", timeout)
                self.returncode = -15
            return self.returncode

    monkeypatch.setattr(runtime_module.subprocess, "Popen", BlockedEncoder)
    runtime = CompositedExportRuntime()
    source_frame = DecodedSourceFrame(
        32, 18, bytes((33, 76, 47, 255)) * (32 * 18)
    )
    runtime_module.register_compositor_fonts()
    # Resolved here, on the main thread. _bundled() skips when there is no
    # ffmpeg, and pytest.skip() raises - from inside the worker below that
    # cannot skip anything, it just kills the thread before it writes, and
    # the wait further down then fails as a bare "assert False". CI has no
    # bundled ffmpeg, so this is the runner's default path, not an edge.
    ffmpeg = _bundled("ffmpeg")

    errors: list[BaseException] = []

    def export() -> None:
        try:
            export_composited_sequence(
                ffmpeg, source, output, plan, inputs,
                SOCIAL_1080P.with_hardware("h264_nvenc"),
                frame_loader=lambda _position: source_frame,  # type: ignore[arg-type]
                runtime=runtime,
            )
        except BaseException as exc:  # proof captures the cancellation type
            errors.append(exc)

    export_thread = threading.Thread(target=export)
    export_thread.start()
    # Reported with whatever the worker raised. This assert used to read
    # "assert False" and nothing else, which said the encoder was never
    # written but not that the thread had died on the line above.
    assert write_entered.wait(5), f"encoder stdin never written; worker raised {errors}"
    assert len(tuple(tmp_path.glob("*.part.mp4"))) == 1
    cancel_thread = threading.Thread(target=runtime.cancel)
    started = time.monotonic()
    cancel_thread.start()
    cancel_thread.join(1)
    assert not cancel_thread.is_alive()
    assert time.monotonic() - started < 1
    assert signals and signals[0] == "terminate"
    export_thread.join(5)
    assert not export_thread.is_alive()
    assert [type(e) for e in errors] == [ExportCancelledError]
    assert not output.exists()
    assert not tuple(tmp_path.glob("*.part.mp4"))


def test_blocked_read_cancel_maps_to_cancelled_and_removes_unique_part(
        tmp_path):
    source = _source_video(tmp_path)
    plan, inputs = _fixture()
    output = tmp_path / "cancelled-blocked-read.mp4"
    event = threading.Event()
    entered_read = threading.Event()

    class EventBlockedStream:
        def read(self, _byte_count):
            entered_read.set()
            event.wait(5)
            return b""

    def blocked_loader(_position: int) -> bytes:
        return runtime_module._read_exact(  # noqa: SLF001
            EventBlockedStream(), 1, event
        )

    # Resolved here, on the main thread. _bundled() skips when there is no
    # ffmpeg, and pytest.skip() raises - from inside the worker below that
    # cannot skip anything, it just kills the thread before it writes, and
    # the wait further down then fails as a bare "assert False". CI has no
    # bundled ffmpeg, so this is the runner's default path, not an edge.
    ffmpeg = _bundled("ffmpeg")

    errors: list[BaseException] = []

    def export() -> None:
        try:
            export_composited_sequence(
                ffmpeg, source, output, plan, inputs,
                SOCIAL_1080P, cancel_event=event,
                frame_loader=blocked_loader,  # type: ignore[arg-type]
            )
        except BaseException as exc:  # proof captures the cancellation type
            errors.append(exc)

    thread = threading.Thread(target=export)
    thread.start()
    assert entered_read.wait(5)
    event.set()
    thread.join(5)
    assert not thread.is_alive()
    assert [type(e) for e in errors] == [ExportCancelledError]
    assert not output.exists()
    assert not tuple(tmp_path.glob("*.part.mp4"))


def test_cancellation_during_source_decode_is_not_misreported_as_corruption(
        tmp_path):
    source = _source_video(tmp_path)
    plan, inputs = _fixture()
    output = tmp_path / "cancel-race.mp4"
    event = threading.Event()

    def cancelled_loader(_position: int) -> bytes:
        event.set()
        raise ExportCancelledError("cancelled during decode")

    with pytest.raises(ExportCancelledError):
        export_composited_sequence(
            _bundled("ffmpeg"), source, output, plan, inputs,
            SOCIAL_1080P, cancel_event=event,
            frame_loader=cancelled_loader,  # type: ignore[arg-type]
        )
    assert not output.exists()


def test_compositor_frame_error_is_a_controlled_export_error(tmp_path):
    source = _source_video(tmp_path)
    plan, inputs = _fixture()
    output = tmp_path / "bad-frame.mp4"
    with pytest.raises(ExportError, match="could not be rendered"):
        export_composited_sequence(
            _bundled("ffmpeg"), source, output, plan, inputs,
            SOCIAL_1080P,
            frame_loader=lambda _position: b"not an encoded image",  # type: ignore[arg-type]
        )
    assert not output.exists()
    assert not tuple(tmp_path.glob("*.part.mp4"))


def test_encoder_broken_pipe_is_controlled_and_removes_unique_part(
        tmp_path, monkeypatch):
    source = _source_video(tmp_path)
    plan, inputs = _fixture()
    output = tmp_path / "broken-pipe.mp4"

    def invalid_encoder(
        ffmpeg_path, _audio, part_path, *, width, height, frame_rate, preset,
    ):
        del preset
        return [
            ffmpeg_path,
            "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pixel_format", "rgba",
            "-video_size", f"{width}x{height}",
            "-framerate", str(frame_rate), "-i", "pipe:0",
            "-c:v", "definitely_not_an_encoder", str(part_path),
        ]

    monkeypatch.setattr(
        runtime_module.ffmpeg_service,
        "build_composited_command",
        invalid_encoder,
    )
    with pytest.raises(ExportError, match="stopped|failed"):
        export_composited_sequence(
            _bundled("ffmpeg"), source, output, plan, inputs, SOCIAL_1080P
        )
    assert not output.exists()
    assert not tuple(tmp_path.glob("*.part.mp4"))


def test_no_voiceover_encode_is_gated_until_soundtrack_policy_exists(tmp_path):
    source = _source_video(tmp_path)
    plan, inputs = _no_voiceover_fixture()
    output = tmp_path / "no-voiceover.mp4"
    with pytest.raises(ExportError, match="soundtrack policy"):
        export_composited_sequence(
            _bundled("ffmpeg"), source, output, plan, inputs, SOCIAL_1080P
        )
    assert not output.exists()
    assert not tuple(tmp_path.glob("*.part.mp4"))


def test_non_frame_aligned_audio_duration_stays_within_one_video_frame(tmp_path):
    source = _source_video(tmp_path)
    plan, inputs = _fixture(frame_count=4_801)
    output = tmp_path / "unaligned.mp4"
    export_composited_sequence(
        _bundled("ffmpeg"), source, output, plan, inputs, SOCIAL_1080P
    )
    completed = subprocess.run(
        [
            _bundled("ffprobe"), "-v", "error", "-show_streams",
            "-show_format", "-of", "json", str(output),
        ],
        capture_output=True,
        check=True,
        creationflags=ffmpeg_service.CREATE_NO_WINDOW,
        timeout=30,
    )
    metadata = json.loads(completed.stdout)
    expected = 4_801 / 48_000
    assert abs(float(metadata["format"]["duration"]) - expected) <= 1 / 30
    for stream in metadata["streams"]:
        if stream["codec_type"] in {"audio", "video"}:
            assert abs(float(stream["duration"]) - expected) <= 1 / 30
