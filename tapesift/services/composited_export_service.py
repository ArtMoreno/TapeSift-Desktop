"""Bundled-FFmpeg execution for immutable composited exports.

Source timestamps are first mapped to an indexed source-frame PTS using a
greatest-PTS-less-than-or-equal lookup. Monotonic spans then stream exact raw
frames from one decoder, so VFR footage never selects a future frame and a
long take does not launch one FFmpeg process per output frame.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import OrderedDict, deque
from dataclasses import dataclass
from fractions import Fraction
import json
import logging
from pathlib import Path
import subprocess
import tempfile
import threading
from typing import Callable, Iterable
import uuid

from PySide6.QtGui import QGuiApplication, QImage

from tapesift.core.exceptions import ExportCancelledError, ExportError
from tapesift.models.composition_plan import CompositionPlan
from tapesift.models.export_settings import ExportPreset
from tapesift.services import ffmpeg_service
from tapesift.services.presentation_sequence_renderer import (
    CompositionSequenceInputs,
    CompositionSequenceRenderer,
    PresentationSequenceCancelled,
    PresentationSequenceError,
)
from tapesift.services.preview_compositor import (
    DecodedSourceFrame,
    PreviewCompositorError,
    register_compositor_fonts,
)


__all__ = (
    "CompositedExportRuntime",
    "RandomAccessFFmpegFrameLoader",
    "export_composited_sequence",
)


log = logging.getLogger(__name__)
ProgressCallback = Callable[[float], None]

_PTS_PROBE_TIMEOUT_SECONDS = 120
_FRAME_DECODE_TIMEOUT_SECONDS = 120
_ENCODER_WAIT_TIMEOUT_SECONDS = 120
_TERMINATE_TIMEOUT_SECONDS = 2
_KILL_TIMEOUT_SECONDS = 2
_DEFAULT_BATCH_ENTRIES = 512
_DEFAULT_BATCH_SOURCE_FRAMES = 128
_DEFAULT_BATCH_WINDOW_MS = 4_000
_SEEK_PREROLL_MS = 2_000
_STDERR_TAIL_LINES = 256


@dataclass(frozen=True, slots=True)
class _IndexedFrame:
    pts: int
    relative_us: int


@dataclass(frozen=True, slots=True)
class _VideoPtsIndex:
    time_base: Fraction
    frames: tuple[_IndexedFrame, ...]
    relative_times_us: tuple[int, ...]
    multiplicities: tuple[int, ...]
    width: int
    height: int

    def frame_index_at_ms(self, position_ms: int) -> int:
        index = bisect_right(
            self.relative_times_us, position_ms * 1_000
        ) - 1
        return max(0, min(len(self.frames) - 1, index))


@dataclass(frozen=True, slots=True)
class _PlannedSegment:
    start: int
    end: int
    direction: int


@dataclass(slots=True)
class _RawDecoder:
    process: subprocess.Popen[bytes]
    stderr_tail: deque[bytes]
    stderr_thread: threading.Thread
    next_index: int
    end_index: int


class _ProcessOwner:
    """Own every subprocess in one job and close cancellation races."""

    def __init__(self, cancel_event: threading.Event) -> None:
        self.cancel_event = cancel_event
        self._lock = threading.RLock()
        self._processes: set[subprocess.Popen[bytes]] = set()

    def start(self, command: list[str], **kwargs) -> subprocess.Popen[bytes]:
        if self.cancel_event.is_set():
            raise ExportCancelledError("Export was cancelled.")
        try:
            process = subprocess.Popen(command, **kwargs)
        except OSError as exc:
            if self.cancel_event.is_set():
                raise ExportCancelledError("Export was cancelled.") from exc
            raise
        # Cancellation can happen while Popen itself is constructing. Publish
        # the process under the lock, then re-check the event before returning.
        with self._lock:
            self._processes.add(process)
            cancelled = self.cancel_event.is_set()
        if cancelled:
            self.terminate(process)
            raise ExportCancelledError("Export was cancelled.")
        return process

    def release(self, process: subprocess.Popen[bytes]) -> None:
        with self._lock:
            self._processes.discard(process)

    def cancel(self) -> None:
        self.cancel_event.set()
        with self._lock:
            processes = tuple(self._processes)
        for process in processes:
            self.terminate(process)

    def terminate(self, process: subprocess.Popen[bytes]) -> None:
        _terminate_bounded(process)
        self.release(process)

    def close(self) -> None:
        with self._lock:
            processes = tuple(self._processes)
        for process in processes:
            self.terminate(process)


class CompositedExportRuntime:
    """One cancellable process owner for one immutable export job."""

    def __init__(
        self,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.cancel_event = cancel_event or threading.Event()
        self._owner = _ProcessOwner(self.cancel_event)
        self._state_lock = threading.Lock()
        self._active = False

    def cancel(self) -> None:
        self._owner.cancel()

    def _begin(self) -> None:
        with self._state_lock:
            if self._active:
                raise RuntimeError(
                    "A composited export runtime cannot run two jobs at once."
                )
            if self.cancel_event.is_set():
                raise ExportCancelledError("Export was cancelled.")
            self._active = True

    def _finish(self) -> None:
        self._owner.close()
        with self._state_lock:
            self._active = False


class _PtsIndexedFrameLoaderBase:
    """Build one presentation-order source PTS index and frozen request plan."""

    def __init__(
        self,
        ffmpeg_path: str,
        source_path: Path,
        *,
        ffprobe_path: str | None = None,
        cancel_event: threading.Event | None = None,
        cache_entries: int = 32,
        batch_entries: int = _DEFAULT_BATCH_ENTRIES,
        batch_source_frames: int = _DEFAULT_BATCH_SOURCE_FRAMES,
        batch_window_ms: int = _DEFAULT_BATCH_WINDOW_MS,
        process_owner: _ProcessOwner | None = None,
    ) -> None:
        self.ffmpeg_path = str(ffmpeg_path)
        self.ffprobe_path = ffprobe_path or _sibling_ffprobe(ffmpeg_path)
        self.source_path = Path(source_path)
        self.cancel_event = cancel_event or threading.Event()
        self._owner = process_owner or _ProcessOwner(self.cancel_event)
        for label, value in (
            ("Source-frame cache size", cache_entries),
            ("Source-frame batch size", batch_entries),
            ("Source-frame decode window", batch_source_frames),
            ("Source-frame batch window", batch_window_ms),
        ):
            if isinstance(value, bool) or not isinstance(value, int) \
                    or value < 1:
                raise ValueError(f"{label} must be positive")
        self.cache_entries = cache_entries
        self.batch_entries = batch_entries
        self.batch_source_frames = batch_source_frames
        self.batch_window_ms = batch_window_ms
        self._cache: OrderedDict[int, DecodedSourceFrame] = OrderedDict()
        self._index: _VideoPtsIndex | None = None
        self._planned_positions: tuple[int, ...] = ()
        self._planned_indexes: tuple[int, ...] = ()
        self._planned_cursor = 0
        self.decoder_process_count = 0

    def __call__(self, source_position_ms: int) -> DecodedSourceFrame:
        return self.load(source_position_ms)

    def prepare_requests(self, positions_ms: Iterable[int]) -> None:
        positions = tuple(positions_ms)
        for position in positions:
            _validate_position(position)
        index = self._ensure_index()
        self._planned_positions = positions
        self._planned_indexes = tuple(
            index.frame_index_at_ms(position) for position in positions
        )
        self._planned_cursor = 0

    def load(self, source_position_ms: int) -> DecodedSourceFrame:
        raise NotImplementedError

    def cancel(self) -> None:
        self._owner.cancel()

    def clear(self) -> None:
        self._cache.clear()
        self._planned_positions = ()
        self._planned_indexes = ()
        self._planned_cursor = 0
        self._index = None

    def _ensure_index(self) -> _VideoPtsIndex:
        if self._index is not None:
            return self._index
        if not self.source_path.is_file():
            raise ExportError(
                f"The source video is missing: {self.source_path}",
                "Relink the source video before exporting.",
            )
        command = [
            self.ffprobe_path,
            "-v", "error",
            "-select_streams", "v:0",
            # Packet PTS is the integer presentation clock consumed by the
            # decoder/filter below. Reading the demux index is orders of
            # magnitude faster than decoding an hour of frames merely to ask
            # FFprobe for best_effort_timestamp (the real 54-minute fixture
            # exceeded two minutes). B-frame packet order is normalized by
            # sorting PTS; an exact regression compares it with decoded frame
            # timestamps before this path is accepted.
            "-show_entries", "stream=time_base,start_pts,width,height:packet=pts",
            "-show_packets",
            "-of", "json",
            str(self.source_path),
        ]
        try:
            process = self._owner.start(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=ffmpeg_service.CREATE_NO_WINDOW,
            )
        except OSError as exc:
            raise ExportError(
                f"Could not start FFprobe source indexer: {exc}",
                "Check the bundled FFmpeg installation.",
            ) from exc
        try:
            stdout, stderr = process.communicate(
                timeout=_PTS_PROBE_TIMEOUT_SECONDS
            )
        except subprocess.TimeoutExpired as exc:
            self._owner.terminate(process)
            raise ExportError(
                "Source video PTS indexing timed out.",
                "The source video could not be indexed for Signature export.",
            ) from exc
        finally:
            self._owner.release(process)
        self._raise_if_cancelled()
        if process.returncode != 0:
            raise ExportError(
                "FFprobe could not index source video frames.\n"
                + _stderr_tail(stderr),
                "The source video could not be indexed for Signature export.",
            )
        try:
            payload = json.loads(stdout)
            streams = payload["streams"]
            time_base_text = streams[0]["time_base"]
            numerator_text, denominator_text = time_base_text.split("/", 1)
            time_base = Fraction(int(numerator_text), int(denominator_text))
            width = int(streams[0]["width"])
            height = int(streams[0]["height"])
            packet_pts = tuple(int(packet["pts"]) for packet in payload["packets"])
            raw_start_pts = streams[0].get("start_pts")
            displayed_start_pts = (
                int(raw_start_pts)
                if raw_start_pts not in (None, "N/A")
                else min(packet_pts)
            )
            # MP4 edit lists can retain negative decoder pre-roll packets that
            # are never presented (the real fixture has two). Stream start_pts
            # is the normalized first displayed timestamp and prevents those
            # hidden packets from poisoning the floor lookup/select count.
            probed_pts = tuple(
                pts for pts in packet_pts if pts >= displayed_start_pts
            )
        except (
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            ZeroDivisionError,
            json.JSONDecodeError,
        ) as exc:
            raise ExportError(
                "FFprobe returned an invalid source-frame PTS index.",
                "The source video could not be indexed for Signature export.",
            ) from exc
        if not probed_pts:
            raise ExportError(
                "Source video frame PTS values are missing.",
                "Transcode or relink the source video before exporting.",
            )
        if width <= 0 or height <= 0:
            raise ExportError(
                "Source video dimensions are invalid.",
                "Transcode or relink the source video before exporting.",
            )
        # Packet order is decode order for B-frame codecs, while packet PTS is
        # presentation time. Sorting and grouping preserves every frame while
        # giving lookup one unambiguous presentation timeline. At an equal PTS,
        # the last delivered frame is the one left on screen.
        ordered_pts = tuple(sorted(probed_pts))
        unique_pts: list[int] = []
        multiplicities: list[int] = []
        for pts in ordered_pts:
            if unique_pts and unique_pts[-1] == pts:
                multiplicities[-1] += 1
            else:
                unique_pts.append(pts)
                multiplicities.append(1)
        pts_values = tuple(unique_pts)
        first_pts = pts_values[0]
        frames = tuple(
            _IndexedFrame(
                pts=pts,
                relative_us=(
                    (pts - first_pts)
                    * time_base.numerator
                    * 1_000_000
                    // time_base.denominator
                ),
            )
            for pts in pts_values
        )
        self._index = _VideoPtsIndex(
            time_base=time_base,
            frames=frames,
            relative_times_us=tuple(frame.relative_us for frame in frames),
            multiplicities=tuple(multiplicities),
            width=width,
            height=height,
        )
        return self._index

    def _raise_if_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise ExportCancelledError("Export was cancelled.")


class RandomAccessFFmpegFrameLoader(_PtsIndexedFrameLoaderBase):
    """Stream exact source PTS as immutable raw RGBA frames.

    A monotonic forward replay segment owns one FFmpeg process. Reverse spans
    are decoded in bounded forward chunks and then served backward. Direction
    changes and jumps outside the protected shuttle envelope start a new
    process; paused/repeated positions reuse the current immutable frame.
    """

    _MAX_CONTINUOUS_STEP_US = 500_000
    _REVERSE_MEMORY_BYTES = 64 * 1024 * 1024

    def __init__(
        self,
        ffmpeg_path: str,
        source_path: Path,
        *,
        ffprobe_path: str | None = None,
        cancel_event: threading.Event | None = None,
        cache_entries: int = 4,
        batch_entries: int = _DEFAULT_BATCH_ENTRIES,
        batch_source_frames: int = _DEFAULT_BATCH_SOURCE_FRAMES,
        batch_window_ms: int = _DEFAULT_BATCH_WINDOW_MS,
        process_owner: _ProcessOwner | None = None,
    ) -> None:
        super().__init__(
            ffmpeg_path,
            source_path,
            ffprobe_path=ffprobe_path,
            cancel_event=cancel_event,
            cache_entries=cache_entries,
            batch_entries=batch_entries,
            batch_source_frames=batch_source_frames,
            batch_window_ms=batch_window_ms,
            process_owner=process_owner,
        )
        self._cache: OrderedDict[int, DecodedSourceFrame] = OrderedDict()
        self._segments: tuple[_PlannedSegment, ...] = ()
        self._segment_by_cursor: tuple[_PlannedSegment, ...] = ()
        self._active_segment: _PlannedSegment | None = None
        self._active_decoder: _RawDecoder | None = None
        self._last_index: int | None = None
        self._last_frame: DecodedSourceFrame | None = None
        self._reverse_frames: dict[int, DecodedSourceFrame] = {}
        self.peak_reverse_cache_bytes = 0

    def prepare_requests(self, positions_ms: Iterable[int]) -> None:
        self._discard_active_decoder()
        super().prepare_requests(positions_ms)
        self._segments = self._build_segments(self._planned_indexes)
        by_cursor: list[_PlannedSegment] = []
        for segment in self._segments:
            by_cursor.extend([segment] * (segment.end - segment.start))
        self._segment_by_cursor = tuple(by_cursor)
        self._last_index = None
        self._last_frame = None
        self._reverse_frames.clear()

    def load(self, source_position_ms: int) -> DecodedSourceFrame:
        _validate_position(source_position_ms)
        self._raise_if_cancelled()
        index = self._ensure_index()
        planned = self._planned_cursor < len(self._planned_positions)
        if not planned:
            return self._load_unplanned(index.frame_index_at_ms(source_position_ms))

        expected = self._planned_positions[self._planned_cursor]
        if source_position_ms != expected:
            raise ExportError(
                "Source-frame requests diverged from the frozen replay plan.",
                "The composited export could not preserve its frame order.",
            )
        frame_index = self._planned_indexes[self._planned_cursor]
        segment = self._segment_by_cursor[self._planned_cursor]
        if segment.direction < 0:
            frame = self._load_reverse(segment, frame_index)
        else:
            frame = self._load_forward(segment, frame_index)

        self._planned_cursor += 1
        if self._planned_cursor == segment.end:
            if segment.direction >= 0:
                self._finish_active_decoder()
            self._active_segment = None
            self._reverse_frames.clear()
        self._remember(frame_index, frame)
        return frame

    def clear(self) -> None:
        self._discard_active_decoder()
        super().clear()
        self._segments = ()
        self._segment_by_cursor = ()
        self._active_segment = None
        self._last_index = None
        self._last_frame = None
        self._reverse_frames.clear()

    def _load_forward(
        self,
        segment: _PlannedSegment,
        frame_index: int,
    ) -> DecodedSourceFrame:
        if self._active_segment != segment:
            self._discard_active_decoder()
            first = self._planned_indexes[segment.start]
            last = self._planned_indexes[segment.end - 1]
            self._active_decoder = self._open_raw_decoder(first, last)
            self._active_segment = segment
            self._last_index = None
            self._last_frame = None
        if self._last_index == frame_index and self._last_frame is not None:
            return self._last_frame
        assert self._active_decoder is not None
        frame = self._read_through(self._active_decoder, frame_index)
        self._last_index = frame_index
        self._last_frame = frame
        return frame

    def _load_reverse(
        self,
        segment: _PlannedSegment,
        frame_index: int,
    ) -> DecodedSourceFrame:
        if self._active_segment != segment:
            self._discard_active_decoder()
            self._active_segment = segment
            self._reverse_frames.clear()
        cached = self._reverse_frames.get(frame_index)
        if cached is None:
            self._reverse_frames = self._decode_reverse_chunk(
                self._planned_cursor, segment
            )
            cached = self._reverse_frames.get(frame_index)
        if cached is None:
            raise ExportError(
                "Reverse source-frame chunk omitted the requested PTS.",
                "The source video could not be decoded for Signature export.",
            )
        return cached

    def _load_unplanned(self, frame_index: int) -> DecodedSourceFrame:
        cached = self._cache.get(frame_index)
        if cached is not None:
            self._cache.move_to_end(frame_index)
            return cached
        self._discard_active_decoder()
        decoder = self._open_raw_decoder(frame_index, frame_index)
        self._active_decoder = decoder
        frame = self._read_through(decoder, frame_index)
        self._finish_active_decoder()
        self._remember(frame_index, frame)
        return frame

    def _decode_reverse_chunk(
        self,
        cursor: int,
        segment: _PlannedSegment,
    ) -> dict[int, DecodedSourceFrame]:
        index = self._ensure_index()
        frame_bytes = index.width * index.height * 4
        memory_frames = max(1, self._REVERSE_MEMORY_BYTES // frame_bytes)
        source_limit = min(self.batch_source_frames, memory_frames)
        requested: list[int] = []
        seen: set[int] = set()
        minimum: int | None = None
        maximum: int | None = None
        first_us: int | None = None
        last_us: int | None = None
        for frame_index in self._planned_indexes[cursor:segment.end]:
            if frame_index in seen:
                continue
            candidate_min = frame_index if minimum is None else min(minimum, frame_index)
            candidate_max = frame_index if maximum is None else max(maximum, frame_index)
            frame_us = index.frames[frame_index].relative_us
            candidate_first_us = frame_us if first_us is None else min(first_us, frame_us)
            candidate_last_us = frame_us if last_us is None else max(last_us, frame_us)
            if requested and (
                len(requested) >= self.batch_entries
                or candidate_max - candidate_min + 1 > source_limit
                or candidate_last_us - candidate_first_us
                > self.batch_window_ms * 1_000
            ):
                break
            requested.append(frame_index)
            seen.add(frame_index)
            minimum = candidate_min
            maximum = candidate_max
            first_us = candidate_first_us
            last_us = candidate_last_us
        assert minimum is not None and maximum is not None
        decoder = self._open_raw_decoder(minimum, maximum)
        self._active_decoder = decoder
        wanted = set(requested)
        result: dict[int, DecodedSourceFrame] = {}
        for value in range(minimum, maximum + 1):
            frame = self._read_through(decoder, value)
            if value in wanted:
                result[value] = frame
        self._finish_active_decoder()
        self.peak_reverse_cache_bytes = max(
            self.peak_reverse_cache_bytes,
            len(result) * frame_bytes,
        )
        return result

    def _open_raw_decoder(self, start_index: int, end_index: int) -> _RawDecoder:
        index = self._ensure_index()
        first = index.frames[start_index]
        last = index.frames[end_index]
        seek_ms = max(0, first.relative_us // 1_000 - _SEEK_PREROLL_MS)
        absolute_end = Fraction(
            last.pts * index.time_base.numerator,
            index.time_base.denominator,
        ) + 1
        output_frames = sum(
            index.multiplicities[value]
            for value in range(start_index, end_index + 1)
        )
        command = [
            self.ffmpeg_path,
            "-hide_banner", "-loglevel", "error", "-copyts",
        ]
        if seek_ms > 0:
            command += ["-ss", f"{seek_ms / 1_000:.9f}"]
        command += [
            "-i", str(self.source_path),
            "-to", _decimal_seconds(absolute_end),
            "-map", "0:v:0",
            "-vf", f"select=between(pts\\,{first.pts}\\,{last.pts})",
            "-fps_mode", "passthrough",
            "-frames:v", str(output_frames),
            "-an", "-sn", "-dn",
            "-pix_fmt", "rgba",
            "-f", "rawvideo",
            "pipe:1",
        ]
        try:
            process = self._owner.start(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # Keep Python's BufferedReader. On Windows an unbuffered pipe
                # can surface FFmpeg's raw frame as thousands of 4 KiB reads;
                # _read_exact would then allocate/join all of those chunks for
                # every frame. BufferedReader.read(frame_size) coalesces them
                # in C while process termination still interrupts the read.
                bufsize=-1,
                creationflags=ffmpeg_service.CREATE_NO_WINDOW,
            )
        except OSError as exc:
            raise ExportError(
                f"Could not start persistent FFmpeg source decoder: {exc}",
                "Check the FFmpeg path in Settings > FFmpeg.",
            ) from exc
        assert process.stdout is not None
        stderr_tail: deque[bytes] = deque(maxlen=_STDERR_TAIL_LINES)
        decoder = _RawDecoder(
            process=process,
            stderr_tail=stderr_tail,
            stderr_thread=_drain_bytes(process.stderr, stderr_tail),
            next_index=start_index,
            end_index=end_index,
        )
        self.decoder_process_count += 1
        return decoder

    def _read_through(
        self,
        decoder: _RawDecoder,
        target_index: int,
    ) -> DecodedSourceFrame:
        index = self._ensure_index()
        if target_index < decoder.next_index:
            if self._last_index == target_index and self._last_frame is not None:
                return self._last_frame
            raise ExportError(
                "Persistent source decoder was asked to move backward.",
                "The frozen replay segmentation is invalid.",
            )
        if target_index > decoder.end_index:
            raise ExportError(
                "Persistent source decoder request exceeds its segment.",
                "The frozen replay segmentation is invalid.",
            )
        frame_size = index.width * index.height * 4
        resolved: DecodedSourceFrame | None = None
        while decoder.next_index <= target_index:
            current = decoder.next_index
            rgba: bytes | None = None
            for _ in range(index.multiplicities[current]):
                rgba = _read_exact(
                    decoder.process.stdout,
                    frame_size,
                    self.cancel_event,
                )
            assert rgba is not None
            resolved = DecodedSourceFrame(index.width, index.height, rgba)
            decoder.next_index += 1
        assert resolved is not None
        return resolved

    def _finish_active_decoder(self) -> None:
        decoder = self._active_decoder
        if decoder is None:
            return
        self._active_decoder = None
        process = decoder.process
        try:
            try:
                return_code = process.wait(timeout=_FRAME_DECODE_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired as exc:
                self._owner.terminate(process)
                raise ExportError(
                    "Persistent source decoder did not finish in time.",
                    "The source video could not be decoded for Signature export.",
                ) from exc
        finally:
            self._owner.release(process)
            decoder.stderr_thread.join(timeout=5)
            _close_process_streams(process)
        if self.cancel_event.is_set():
            raise ExportCancelledError("Export was cancelled.")
        if return_code != 0:
            raise ExportError(
                "Persistent source decoder failed.\n"
                + _stderr_tail(b"".join(decoder.stderr_tail)),
                "The source video could not be decoded for Signature export.",
            )

    def _discard_active_decoder(self) -> None:
        decoder = self._active_decoder
        self._active_decoder = None
        if decoder is None:
            return
        self._owner.terminate(decoder.process)
        decoder.stderr_thread.join(timeout=5)

    def _remember(self, frame_index: int, frame: DecodedSourceFrame) -> None:
        self._cache[frame_index] = frame
        self._cache.move_to_end(frame_index)
        while len(self._cache) > self.cache_entries:
            self._cache.popitem(last=False)

    def _build_segments(
        self,
        indexes: tuple[int, ...],
    ) -> tuple[_PlannedSegment, ...]:
        if not indexes:
            return ()
        index = self._ensure_index()
        segments: list[_PlannedSegment] = []
        start = 0
        direction = 0
        for cursor in range(1, len(indexes)):
            previous = indexes[cursor - 1]
            current = indexes[cursor]
            delta = current - previous
            step_direction = 1 if delta > 0 else -1 if delta < 0 else 0
            gap_us = abs(
                index.frames[current].relative_us
                - index.frames[previous].relative_us
            )
            changed_direction = (
                direction != 0
                and step_direction != 0
                and step_direction != direction
            )
            if gap_us > self._MAX_CONTINUOUS_STEP_US or changed_direction:
                segments.append(_PlannedSegment(
                    start=start,
                    end=cursor,
                    direction=direction or 1,
                ))
                start = cursor
                direction = 0
            elif step_direction != 0 and direction == 0:
                direction = step_direction
        segments.append(_PlannedSegment(
            start=start,
            end=len(indexes),
            direction=direction or 1,
        ))
        return tuple(segments)


def export_composited_sequence(
    ffmpeg_path: str,
    source_path: Path,
    output_path: Path,
    plan: CompositionPlan,
    inputs: CompositionSequenceInputs,
    preset: ExportPreset,
    *,
    frame_rate: int = 30,
    cancel_event: threading.Event | None = None,
    on_progress: ProgressCallback | None = None,
    frame_loader: RandomAccessFFmpegFrameLoader | None = None,
    runtime: CompositedExportRuntime | None = None,
) -> str:
    """Compose, encode, and atomically publish one Voiceover package.

    The no-Voiceover renderer contract exists, but publishing it is gated
    until product selects source-audio, mixed-audio, or silent output.
    """

    if QGuiApplication.instance() is None:
        raise ExportError(
            "A Qt application is required for composited export.",
            "Restart TapeSift and try the export again.",
        )
    try:
        register_compositor_fonts()
    except PreviewCompositorError as exc:
        raise ExportError(
            f"Compositor fonts are not ready: {exc}",
            "Restart TapeSift and try the export again.",
        ) from exc
    if not inputs.has_voiceover:
        raise ExportError(
            "No-Voiceover composited export has no selected soundtrack policy.",
            "Enable Voiceover; source-audio policy is not yet defined.",
        )

    ffmpeg = Path(ffmpeg_path)
    if not ffmpeg.is_file():
        raise ExportError(
            f"FFmpeg is missing: {ffmpeg_path}",
            "Check the FFmpeg path in Settings > FFmpeg.",
        )
    source = Path(source_path)
    if not source.is_file():
        raise ExportError(
            f"The source video is missing: {source}",
            "Relink the source video before exporting.",
        )
    output = Path(output_path)

    if runtime is not None and cancel_event is not None \
            and runtime.cancel_event is not cancel_event:
        raise ValueError(
            "cancel_event must be the runtime's event when both are supplied"
        )
    job = runtime or CompositedExportRuntime(cancel_event)
    job._begin()
    event = job.cancel_event
    owner = job._owner
    stop_watcher = threading.Event()
    watcher = _watch_cancellation(owner, event, stop_watcher)
    loader = frame_loader or RandomAccessFFmpegFrameLoader(
        str(ffmpeg), source, cancel_event=event, process_owner=owner
    )
    part_path = output.with_name(
        f".{output.stem}.{uuid.uuid4().hex}.part{output.suffix}"
    )
    published = False
    process: subprocess.Popen[bytes] | None = None
    command: list[str] | None = None
    try:
        try:
            renderer = CompositionSequenceRenderer(
                plan, inputs, loader, fps=frame_rate
            )
        except PresentationSequenceError as exc:
            raise ExportError(
                f"Recorded presentation is invalid: {exc}",
                "Record a new Voiceover take or choose Clean export.",
            ) from exc
        if isinstance(loader, RandomAccessFFmpegFrameLoader):
            loader.prepare_requests(renderer.source_positions())
        if event.is_set():
            raise ExportCancelledError("Export was cancelled.")
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ExportError(
                f"Could not create the export folder: {exc}",
                "Choose a different export folder.",
            ) from exc

        try:
            temp_context = tempfile.TemporaryDirectory(
                prefix="tapesift_composite_", dir=output.parent
            )
        except OSError as exc:
            raise ExportError(
                f"Could not create composited export workspace: {exc}",
                "Choose a writable export folder.",
            ) from exc
        with temp_context as temp_name:
            audio_path = Path(temp_name) / "voiceover.wav"
            assert inputs.voiceover_audio is not None
            try:
                audio_path.write_bytes(inputs.voiceover_audio)
            except OSError as exc:
                raise ExportError(
                    f"Could not stage Voiceover audio: {exc}",
                    "Choose a writable export folder.",
                ) from exc
            try:
                command = ffmpeg_service.build_composited_command(
                    str(ffmpeg),
                    audio_path,
                    part_path,
                    width=plan.canvas.width,
                    height=plan.canvas.height,
                    frame_rate=frame_rate,
                    preset=preset,
                )
            except ValueError as exc:
                raise ExportError(
                    f"Composited encoder settings are invalid: {exc}",
                    "Choose Source Quality or Social 1080p for this export.",
                ) from exc

            try:
                process = owner.start(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=ffmpeg_service.CREATE_NO_WINDOW,
                )
            except OSError as exc:
                raise ExportError(
                    f"Could not start FFmpeg compositor encoder: {exc}",
                    "Check the FFmpeg path in Settings > FFmpeg.",
                ) from exc

            stdout_tail: deque[bytes] = deque(maxlen=32)
            stderr_tail: deque[bytes] = deque(maxlen=_STDERR_TAIL_LINES)
            threads = (
                _drain_bytes(process.stdout, stdout_tail),
                _drain_bytes(process.stderr, stderr_tail),
            )
            try:
                assert process.stdin is not None
                for rendered in renderer.frames(is_cancelled=event.is_set):
                    if event.is_set():
                        raise PresentationSequenceCancelled(
                            "Presentation export was cancelled."
                        )
                    rgba = rendered.image.convertToFormat(
                        QImage.Format.Format_RGBA8888
                    )
                    expected = rgba.width() * rgba.height() * 4
                    payload = rgba.constBits().tobytes()
                    if len(payload) != expected:
                        raise ExportError(
                            "Compositor frame memory is not tightly packed.",
                            "The Signature export could not be encoded.",
                        )
                    _write_all(process.stdin, payload, event)
                    if on_progress is not None:
                        on_progress(
                            ((rendered.index + 1) / renderer.frame_count)
                            * 100.0
                        )
                process.stdin.close()
                try:
                    return_code = process.wait(
                        timeout=_ENCODER_WAIT_TIMEOUT_SECONDS
                    )
                except subprocess.TimeoutExpired as exc:
                    owner.terminate(process)
                    raise ExportError(
                        "FFmpeg compositor encoder did not finish in time.",
                        "See the export queue error for encoder details.",
                    ) from exc
            except PresentationSequenceCancelled as exc:
                owner.cancel()
                raise ExportCancelledError("Export was cancelled.") from exc
            except PresentationSequenceError as exc:
                owner.terminate(process)
                if event.is_set():
                    raise ExportCancelledError("Export was cancelled.") from exc
                raise ExportError(
                    f"Recorded presentation could not be rendered: {exc}",
                    "See the export queue error for compositor details.",
                ) from exc
            except (BrokenPipeError, OSError) as exc:
                owner.terminate(process)
                if event.is_set():
                    raise ExportCancelledError("Export was cancelled.") from exc
                raise ExportError(
                    "FFmpeg stopped while receiving composited frames.",
                    "See the export queue error for encoder details.",
                ) from exc
            except BaseException:
                owner.terminate(process)
                raise
            finally:
                if process.stdin is not None and not process.stdin.closed:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass
                owner.release(process)
                for thread in threads:
                    thread.join(timeout=5)
                _close_process_streams(process)

            if event.is_set():
                raise ExportCancelledError("Export was cancelled.")
            if return_code != 0 or not part_path.is_file():
                raise ExportError(
                    f"FFmpeg composited encode failed (code {return_code}).\n"
                    + _stderr_tail(b"".join(stderr_tail)),
                    "See the export queue error for encoder details.",
                )

        if event.is_set():
            raise ExportCancelledError("Export was cancelled.")
        try:
            part_path.replace(output)
        except OSError as exc:
            raise ExportError(
                f"Could not publish the completed export: {exc}",
                "Check the export folder permissions and free space.",
            ) from exc
        published = True
        if on_progress is not None:
            on_progress(100.0)
        assert command is not None
        return ffmpeg_service.command_to_display_string(command)
    finally:
        stop_watcher.set()
        watcher.join(timeout=1)
        if isinstance(loader, RandomAccessFFmpegFrameLoader):
            loader.clear()
        job._finish()
        if not published:
            try:
                part_path.unlink(missing_ok=True)
            except OSError:
                log.warning(
                    "Could not remove unpublished composited part: %s",
                    part_path,
                    exc_info=True,
                )


def _watch_cancellation(
    owner: _ProcessOwner,
    cancel_event: threading.Event,
    stop_event: threading.Event,
) -> threading.Thread:
    def watch() -> None:
        while not stop_event.wait(0.05):
            if cancel_event.is_set():
                owner.cancel()
                return

    thread = threading.Thread(
        target=watch,
        name="TapeSiftCompositedCancel",
        daemon=True,
    )
    thread.start()
    return thread


def _write_all(stream, payload: bytes, cancel_event: threading.Event) -> None:
    view = memoryview(payload)
    offset = 0
    while offset < len(view):
        if cancel_event.is_set():
            raise PresentationSequenceCancelled(
                "Presentation export was cancelled."
            )
        written = stream.write(view[offset:])
        if written is None or written <= 0:
            raise BrokenPipeError("FFmpeg compositor stdin closed")
        offset += written


def _read_exact(
    stream,
    byte_count: int,
    cancel_event: threading.Event,
) -> bytes:
    chunks: list[bytes] = []
    remaining = byte_count
    while remaining:
        if cancel_event.is_set():
            raise ExportCancelledError("Export was cancelled.")
        try:
            chunk = stream.read(remaining)
        except (OSError, ValueError) as exc:
            if cancel_event.is_set():
                raise ExportCancelledError("Export was cancelled.") from exc
            raise ExportError(
                f"Persistent source decoder pipe failed: {exc}",
                "The source video could not be decoded for Signature export.",
            ) from exc
        if not chunk:
            if cancel_event.is_set():
                raise ExportCancelledError("Export was cancelled.")
            raise ExportError(
                "Persistent source decoder ended before a complete RGBA frame.",
                "The source video could not be decoded for Signature export.",
            )
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _drain_bytes(stream, tail: deque[bytes]) -> threading.Thread:
    def drain() -> None:
        if stream is None:
            return
        try:
            for value in iter(stream.readline, b""):
                tail.append(value)
        except OSError:
            return

    thread = threading.Thread(target=drain, daemon=True)
    thread.start()
    return thread


def _terminate_bounded(process: subprocess.Popen[bytes]) -> None:
    exited = False
    try:
        try:
            if process.poll() is not None:
                exited = True
                return
        except OSError:
            return
        # Signal the process before touching Python's buffered streams. A
        # concurrent BufferedWriter.write can hold the stream lock while the
        # child is blocked reading; closing stdin first would wait on that
        # lock and prevent cancellation from ever reaching terminate().
        try:
            process.terminate()
        except OSError:
            pass
        try:
            process.wait(timeout=_TERMINATE_TIMEOUT_SECONDS)
            exited = True
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=_KILL_TIMEOUT_SECONDS)
            exited = True
        except (OSError, subprocess.TimeoutExpired):
            log.warning("FFmpeg subprocess did not exit after kill.")
    finally:
        # Closing an output stream is safe only after the child has exited and
        # released any blocked concurrent read/write. Normal-success stdin is
        # still closed explicitly by the encoder path before its final wait.
        if exited:
            _close_process_streams(process)


def _close_process_streams(process: subprocess.Popen[bytes]) -> None:
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(process, name, None)
        if stream is None or getattr(stream, "closed", False):
            continue
        close = getattr(stream, "close", None)
        if not callable(close):
            continue
        try:
            close()
        except (OSError, ValueError):
            pass


def _sibling_ffprobe(ffmpeg_path: str) -> str:
    ffmpeg = Path(ffmpeg_path)
    suffix = ffmpeg.suffix or (".exe" if ffmpeg_service.CREATE_NO_WINDOW else "")
    sibling = ffmpeg.with_name("ffprobe" + suffix)
    if sibling.is_file():
        return str(sibling)
    discovered = ffmpeg_service.find_executable("ffprobe")
    if not discovered:
        raise ExportError(
            "FFprobe is missing from the FFmpeg installation.",
            "Repair the bundled FFmpeg installation.",
        )
    return discovered


def _validate_position(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("Source position must be a non-negative integer")


def _decimal_seconds(value: Fraction) -> str:
    return f"{float(value):.9f}"


def _stderr_tail(data: bytes) -> str:
    error = data.decode("utf-8", errors="replace").strip()
    return "\n".join(error.splitlines()[-8:])
