"""Voiceover audio clocks, meters, envelopes and canonical WAV output."""

from __future__ import annotations

from array import array
import io
import wave

import pytest

from tapesift.services.voiceover_service import (
    AudioLevels,
    PcmCaptureBuffer,
    WaveformAccumulator,
    duration_ms_for_frames,
    frame_count_for_bytes,
    pcm16_levels,
    wav_bytes,
)


def _pcm(*samples: int) -> bytes:
    return array("h", samples).tobytes()


def _wav_pcm(value: bytes) -> bytes:
    with wave.open(io.BytesIO(value), "rb") as reader:
        return reader.readframes(reader.getnframes())


def test_frame_count_and_duration_use_audio_frames_only():
    assert frame_count_for_bytes(_pcm(1, 2, 3, 4), channels=1) == 4
    assert frame_count_for_bytes(_pcm(1, 2, 3, 4), channels=2) == 2
    assert duration_ms_for_frames(48_000, 48_000) == 1000
    assert duration_ms_for_frames(24_000, 48_000) == 500


def test_levels_are_normalized_from_real_pcm_samples():
    assert pcm16_levels(b"") == AudioLevels()
    levels = pcm16_levels(_pcm(0, 16384, -32768, 0))
    assert levels.peak == 1.0
    assert levels.rms == pytest.approx((1.25 / 4) ** 0.5, abs=1e-5)


def test_pcm16_rejects_an_incomplete_sample():
    with pytest.raises(ValueError, match="complete samples"):
        pcm16_levels(b"\x01")


def test_waveform_is_independent_of_device_chunk_boundaries():
    pcm = _pcm(100, -200, 300, -400, 500, -600, 700, -800)
    whole = WaveformAccumulator(frames_per_bucket=2)
    whole.append(pcm)
    split = WaveformAccumulator(frames_per_bucket=2)
    split.append(pcm[:4])
    split.append(pcm[4:10])
    split.append(pcm[10:])
    assert split.values() == whole.values()
    assert split.values() == pytest.approx(
        (200 / 32768, 400 / 32768, 600 / 32768, 800 / 32768))


def test_capture_buffer_holds_partial_device_frames_until_complete():
    capture = PcmCaptureBuffer(sample_rate=1000, channels=1, waveform_hz=100)
    sample = _pcm(1234)
    assert capture.append(sample[:1]) == AudioLevels()
    assert capture.frame_count == 0
    levels = capture.append(sample[1:])
    assert capture.frame_count == 1
    assert capture.duration_ms == 1
    assert levels.peak == pytest.approx(1234 / 32768)


def test_stereo_capture_is_independent_of_arbitrary_byte_boundaries():
    pcm = _pcm(100, -200, 300, -400, 500, -600)
    whole = PcmCaptureBuffer(
        sample_rate=1000, channels=2, waveform_hz=1000)
    split = PcmCaptureBuffer(
        sample_rate=1000, channels=2, waveform_hz=1000)

    whole.append(pcm)
    offsets = (1, 4, 7, 9, len(pcm))
    start = 0
    for stop in offsets:
        split.append(pcm[start:stop])
        start = stop

    whole_result = whole.finalize()
    split_result = split.finalize()
    assert _wav_pcm(split_result.wav) == _wav_pcm(whole_result.wav) == pcm
    assert split_result.frame_count == whole_result.frame_count == 3
    assert split_result.duration_ms == whole_result.duration_ms == 3
    assert split_result.waveform == whole_result.waveform
    assert split_result.waveform == pytest.approx(
        (200 / 32768, 400 / 32768, 600 / 32768))


def test_finalize_discards_only_an_incomplete_trailing_frame():
    capture = PcmCaptureBuffer(sample_rate=1000, channels=2, waveform_hz=1000)
    complete_frame = _pcm(123, -456)
    capture.append(complete_frame + b"\xff")

    result = capture.finalize()

    assert _wav_pcm(result.wav) == complete_frame
    assert result.frame_count == 1
    assert result.duration_ms == 1
    assert result.waveform == pytest.approx((456 / 32768,))


def test_finalize_outputs_matching_wav_and_sample_clock():
    capture = PcmCaptureBuffer(sample_rate=48_000, channels=1)
    capture.append(_pcm(*([1200] * 48_000)))
    result = capture.finalize()
    assert result.frame_count == 48_000
    assert result.duration_ms == 1000
    expected_pcm = _pcm(*([1200] * 48_000))
    assert not hasattr(result, "pcm")
    assert len(result.waveform) == 100

    with wave.open(io.BytesIO(result.wav), "rb") as reader:
        assert reader.getframerate() == 48_000
        assert reader.getnchannels() == 1
        assert reader.getsampwidth() == 2
        assert reader.getnframes() == 48_000
        assert reader.readframes(48_000) == expected_pcm


def test_wav_rejects_partial_interleaved_frames():
    with pytest.raises(ValueError, match="complete audio frames"):
        wav_bytes(_pcm(1, 2, 3), channels=2)


def test_wav_helper_wraps_complete_pcm_without_changing_samples():
    pcm = _pcm(1, -2, 3, -4)
    wrapped = wav_bytes(pcm, sample_rate=1_000, channels=2)
    with wave.open(io.BytesIO(wrapped), "rb") as reader:
        assert reader.getframerate() == 1_000
        assert reader.getnchannels() == 2
        assert reader.getnframes() == 2
        assert reader.readframes(2) == pcm
