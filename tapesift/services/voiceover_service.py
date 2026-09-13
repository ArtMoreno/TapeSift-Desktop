"""Deterministic PCM capture primitives for Voiceover.

The Qt audio device/controller is intentionally separate from these helpers.
This module owns the facts that must stay identical in tests, persistence,
preview and export: complete PCM frames, a frame-count clock, real meters,
waveform envelopes and canonical WAV bytes.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass
import io
import math
import sys
import tempfile
import wave

from tapesift.models.presentation_track import PresentationEventTrack


CANONICAL_SAMPLE_RATE = 48_000
CANONICAL_CHANNELS = 1
PCM16_SAMPLE_WIDTH = 2


@dataclass(frozen=True)
class AudioLevels:
    """Normalized live input levels for one complete PCM chunk."""

    peak: float = 0.0
    rms: float = 0.0


@dataclass(frozen=True)
class CapturedAudio:
    """A finalized take before it is assigned project metadata."""

    wav: bytes
    sample_rate: int
    channels: int
    frame_count: int
    duration_ms: int
    waveform: tuple[float, ...]
    presentation_track: PresentationEventTrack | None = None


def pcm16_samples(data: bytes) -> array:
    """Decode little-endian signed 16-bit PCM without losing samples."""
    if len(data) % PCM16_SAMPLE_WIDTH:
        raise ValueError("PCM16 data must contain complete samples")
    samples = array("h")
    samples.frombytes(data)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def pcm16_levels(data: bytes) -> AudioLevels:
    """Return peak and RMS in the inclusive 0..1 display range."""
    samples = pcm16_samples(data)
    if not samples:
        return AudioLevels()
    magnitudes = [abs(int(sample)) for sample in samples]
    peak = min(1.0, max(magnitudes) / 32768.0)
    mean_square = sum(value * value for value in magnitudes) / len(magnitudes)
    rms = min(1.0, math.sqrt(mean_square) / 32768.0)
    return AudioLevels(peak=peak, rms=rms)


def frame_count_for_bytes(data: bytes, channels: int = 1) -> int:
    """Count only complete interleaved PCM16 frames."""
    if channels <= 0:
        raise ValueError("channels must be positive")
    return len(data) // (PCM16_SAMPLE_WIDTH * channels)


def duration_ms_for_frames(frame_count: int, sample_rate: int) -> int:
    """Derive time from captured frames, never from a wall clock."""
    if frame_count < 0:
        raise ValueError("frame_count cannot be negative")
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    return frame_count * 1000 // sample_rate


def wav_bytes(
        pcm: bytes, *, sample_rate: int = CANONICAL_SAMPLE_RATE,
        channels: int = CANONICAL_CHANNELS) -> bytes:
    """Wrap complete PCM16 frames in a canonical little-endian WAV."""
    if sample_rate <= 0 or channels <= 0:
        raise ValueError("sample_rate and channels must be positive")
    frame_size = PCM16_SAMPLE_WIDTH * channels
    if len(pcm) % frame_size:
        raise ValueError("PCM data must contain complete audio frames")
    target = io.BytesIO()
    with wave.open(target, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(PCM16_SAMPLE_WIDTH)
        writer.setframerate(sample_rate)
        writer.writeframes(pcm)
    return target.getvalue()


class WaveformAccumulator:
    """Build a stable peak envelope from arbitrarily chunked PCM input."""

    def __init__(self, frames_per_bucket: int = 480, channels: int = 1) -> None:
        if frames_per_bucket <= 0 or channels <= 0:
            raise ValueError("bucket size and channels must be positive")
        self.frames_per_bucket = int(frames_per_bucket)
        self.channels = int(channels)
        self._bucket: list[int] = []
        self._peaks: list[float] = []

    def append(self, pcm: bytes) -> None:
        samples = pcm16_samples(pcm)
        samples_per_bucket = self.frames_per_bucket * self.channels
        for sample in samples:
            self._bucket.append(abs(int(sample)))
            if len(self._bucket) == samples_per_bucket:
                self._flush_bucket()

    def _flush_bucket(self) -> None:
        if not self._bucket:
            return
        self._peaks.append(min(1.0, max(self._bucket) / 32768.0))
        self._bucket.clear()

    def values(self, *, include_partial: bool = True) -> tuple[float, ...]:
        values = list(self._peaks)
        if include_partial and self._bucket:
            values.append(min(1.0, max(self._bucket) / 32768.0))
        return tuple(values)


class PcmCaptureBuffer:
    """Stream complete frames to a temporary WAV and expose its clock.

    The recording grows on disk rather than in a Python ``bytearray``.  On
    finalization only the canonical WAV is materialized, so a long take never
    exists in memory once as raw PCM and again as a WAV.
    """

    def __init__(
            self, *, sample_rate: int = CANONICAL_SAMPLE_RATE,
            channels: int = CANONICAL_CHANNELS,
            waveform_hz: int = 100) -> None:
        if sample_rate <= 0 or channels <= 0 or waveform_hz <= 0:
            raise ValueError("audio format and waveform rate must be positive")
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self._frame_size = PCM16_SAMPLE_WIDTH * self.channels
        self._frame_count = 0
        self._pending = bytearray()
        self._waveform = WaveformAccumulator(
            max(1, self.sample_rate // waveform_hz), self.channels)
        self._target = tempfile.TemporaryFile(mode="w+b")
        self._writer = wave.open(self._target, "wb")
        self._writer.setnchannels(self.channels)
        self._writer.setsampwidth(PCM16_SAMPLE_WIDTH)
        self._writer.setframerate(self.sample_rate)
        self._finalized = False

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def duration_ms(self) -> int:
        return duration_ms_for_frames(self.frame_count, self.sample_rate)

    @property
    def pending_byte_count(self) -> int:
        """Bytes held only until an arbitrary device chunk completes a frame."""
        return len(self._pending)

    def append(self, data: bytes) -> AudioLevels:
        """Accept any device chunk, committing only complete frames."""
        if self._finalized:
            raise RuntimeError("Cannot append to a finalized capture")
        if not data:
            return AudioLevels()
        combined = bytes(self._pending) + bytes(data)
        complete_size = len(combined) // self._frame_size * self._frame_size
        complete = combined[:complete_size]
        self._pending = bytearray(combined[complete_size:])
        if complete:
            self._writer.writeframesraw(complete)
            self._frame_count += complete_size // self._frame_size
            self._waveform.append(complete)
        return pcm16_levels(complete)

    def finalize(self) -> CapturedAudio:
        """Freeze the take; an incomplete trailing device frame is ignored."""
        if self._finalized:
            raise RuntimeError("Capture has already been finalized")
        self._finalized = True
        try:
            self._writer.close()
            self._target.seek(0)
            wav = self._target.read()
        finally:
            self._target.close()
        return CapturedAudio(
            wav=wav,
            sample_rate=self.sample_rate,
            channels=self.channels,
            frame_count=self.frame_count,
            duration_ms=self.duration_ms,
            waveform=self._waveform.values(),
        )

    def cancel(self) -> None:
        """Discard the temporary recording without materializing audio."""
        if self._finalized:
            return
        self._finalized = True
        try:
            try:
                self._writer.close()
            except Exception:
                pass
        finally:
            try:
                self._target.close()
            except Exception:
                pass

    def __del__(self) -> None:
        try:
            self.cancel()
        except Exception:
            # Destructors run during interpreter shutdown as well as ordinary
            # cleanup; an already-torn-down file object needs no recovery.
            pass
