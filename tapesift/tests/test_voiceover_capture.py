"""Voiceover capture backend selection, state, clocks, and cleanup."""

from __future__ import annotations

from array import array
import io
import json
import wave

import pytest
from PySide6.QtMultimedia import QAudioFormat

from tapesift.models.presentation_track import (
    PresentationEvent,
    PresentationEventKind,
    PresentationEventTrack,
    SourcePositionTransition,
)
from tapesift.services.voiceover_capture import (
    AudioCaptureUnavailable,
    AudioInputDevice,
    VoiceoverCaptureController,
    VoiceoverCapturePolicy,
    VoiceoverCaptureState,
    canonical_audio_format,
    select_audio_input_device,
)


def _pcm(*samples: int) -> bytes:
    return array("h", samples).tobytes()


def _wav_info(value: bytes) -> tuple[int, int, int, int, bytes]:
    with wave.open(io.BytesIO(value), "rb") as reader:
        return (
            reader.getframerate(),
            reader.getnchannels(),
            reader.getsampwidth(),
            reader.getnframes(),
            reader.readframes(reader.getnframes()),
        )


class FakeBackend:
    def __init__(
            self, devices: tuple[AudioInputDevice, ...] | None = None,
            *, start_error: Exception | None = None,
            start_chunk: bytes = b"", final_chunk: bytes = b"",
            final_error: str = "", stop_error: Exception | None = None,
            drain_error: Exception | None = None) -> None:
        self.devices = devices if devices is not None else (
            AudioInputDevice("mic-1", "Desk microphone", is_default=True),
        )
        self.start_error = start_error
        self.start_chunk = start_chunk
        self.final_chunk = final_chunk
        self.final_error = final_error
        self.stop_error = stop_error
        self.drain_error = drain_error
        self.started_device: AudioInputDevice | None = None
        self.start_calls = 0
        self.stop_calls = 0
        self.drain_calls = 0
        self.active = False
        self.pending = bytearray()
        self._on_pcm = None
        self._on_error = None

    def available_devices(self) -> tuple[AudioInputDevice, ...]:
        return self.devices

    def start(self, device, *, on_pcm, on_error) -> None:
        self.start_calls += 1
        if self.start_error is not None:
            raise self.start_error
        self.started_device = device
        self._on_pcm = on_pcm
        self._on_error = on_error
        self.active = True
        if self.start_chunk:
            start_chunk = self.start_chunk
            self.start_chunk = b""
            on_pcm(start_chunk)

    def stop(self) -> None:
        self.stop_calls += 1
        try:
            if self.final_chunk and self._on_pcm is not None:
                final_chunk = self.final_chunk
                self.final_chunk = b""
                self._on_pcm(final_chunk)
            if self.final_error and self._on_error is not None:
                final_error = self.final_error
                self.final_error = ""
                self._on_error(final_error)
            if self.stop_error is not None:
                raise self.stop_error
        finally:
            self.active = False

    def drain(self) -> None:
        self.drain_calls += 1
        if self.drain_error is not None:
            raise self.drain_error
        if self.pending and self._on_pcm is not None:
            pending = bytes(self.pending)
            self.pending.clear()
            self._on_pcm(pending)

    def queue(self, data: bytes) -> None:
        self.pending.extend(data)

    def emit(self, data: bytes) -> None:
        assert self.active
        assert self._on_pcm is not None
        self._on_pcm(data)

    def fail(self, message: str) -> None:
        assert self.active
        assert self._on_error is not None
        self._on_error(message)


def test_canonical_qt_format_is_48k_mono_pcm16():
    audio_format = canonical_audio_format()

    assert audio_format.isValid()
    assert audio_format.sampleRate() == 48_000
    assert audio_format.channelCount() == 1
    assert audio_format.sampleFormat() == QAudioFormat.SampleFormat.Int16
    assert audio_format.bytesPerFrame() == 2


def test_device_selection_prefers_id_then_name_then_default_then_first():
    devices = (
        AudioInputDevice("old", "Unsupported", supports_canonical=False),
        AudioInputDevice("a", "Camera Mic"),
        AudioInputDevice("b", "Desk Mic", is_default=True),
        AudioInputDevice("c", "Headset"),
    )

    assert select_audio_input_device(
        devices, preferred_id="c", preferred_name="Desk Mic").id == "c"
    assert select_audio_input_device(
        devices, preferred_id="stale", preferred_name="desk mic").id == "b"
    assert select_audio_input_device(devices).id == "b"
    assert select_audio_input_device(devices[:2]).id == "a"


def test_device_selection_reports_missing_and_noncanonical_inputs():
    with pytest.raises(AudioCaptureUnavailable, match="No microphone"):
        select_audio_input_device(())
    with pytest.raises(AudioCaptureUnavailable, match="48 kHz"):
        select_audio_input_device((
            AudioInputDevice("x", "Old mic", supports_canonical=False),
        ))


def test_controller_streams_levels_and_final_chunk_to_one_wav():
    backend = FakeBackend(final_chunk=_pcm(3000, -4000))
    controller = VoiceoverCaptureController(backend)
    states = []
    levels = []
    progress = []
    def on_state(state):
        states.append(state)
        if state == VoiceoverCaptureState.FINALIZING:
            assert not backend.active

    controller.state_changed.connect(on_state)
    controller.levels_changed.connect(lambda peak, rms: levels.append(
        (peak, rms)))
    controller.progress_changed.connect(lambda frames, duration: progress.append(
        (frames, duration)))

    assert controller.start()
    backend.emit(_pcm(0, 16384, -32768))
    captured = controller.stop()

    assert captured is not None
    assert states == [
        VoiceoverCaptureState.RECORDING,
        VoiceoverCaptureState.FINALIZING,
        VoiceoverCaptureState.IDLE,
    ]
    assert controller.state == VoiceoverCaptureState.IDLE
    assert controller.selected_device == backend.devices[0]
    assert backend.start_calls == backend.stop_calls == 1
    # Final-drain bytes are captured, but no unstable RECORDING telemetry is
    # emitted from inside the backend stop transition.
    assert levels[-1][0] == 1.0
    assert progress[-1] == (3, 0)
    assert captured.frame_count == 5
    assert captured.duration_ms == 0
    assert not hasattr(captured, "pcm")
    assert _wav_info(captured.wav) == (
        48_000, 1, 2, 5,
        _pcm(0, 16384, -32768, 3000, -4000),
    )


def test_duration_policy_trims_a_chunk_at_the_audio_frame_limit():
    backend = FakeBackend()
    controller = VoiceoverCaptureController(
        backend, policy=VoiceoverCapturePolicy(max_duration_ms=2))
    finished = []
    limits = []
    controller.recording_finished.connect(finished.append)
    controller.duration_limit_reached.connect(lambda: limits.append(True))

    assert controller.start()
    backend.emit(b"\x00" + _pcm(*range(200)))

    assert controller.state == VoiceoverCaptureState.IDLE
    assert limits == [True]
    assert len(finished) == 1
    captured = finished[0]
    assert captured.frame_count == 96
    assert captured.duration_ms == 2
    assert _wav_info(captured.wav)[3] == 96
    assert backend.stop_calls == 1


def test_cancel_discards_take_and_has_explicit_cleanup_states():
    backend = FakeBackend()
    controller = VoiceoverCaptureController(backend)
    states = []
    finished = []
    cancelled = []

    def on_state(state):
        states.append(state)
        if state == VoiceoverCaptureState.FINALIZING:
            assert not backend.active

    controller.state_changed.connect(on_state)
    controller.recording_finished.connect(finished.append)
    controller.recording_cancelled.connect(lambda: cancelled.append(True))

    assert controller.start()
    backend.emit(_pcm(*range(100)))
    assert controller.cancel()

    assert states == [
        VoiceoverCaptureState.RECORDING,
        VoiceoverCaptureState.FINALIZING,
        VoiceoverCaptureState.IDLE,
    ]
    assert cancelled == [True]
    assert finished == []
    assert backend.stop_calls == 1
    assert controller.frame_count == controller.duration_ms == 0
    assert not controller.cancel()


def test_backend_error_cleans_up_and_requires_explicit_reset():
    backend = FakeBackend()
    controller = VoiceoverCaptureController(backend)
    errors = []
    controller.error_occurred.connect(errors.append)

    assert controller.start()
    backend.emit(_pcm(100, 200, 300))
    backend.fail("USB microphone disconnected")

    assert controller.state == VoiceoverCaptureState.ERROR
    assert controller.error_message == "USB microphone disconnected"
    assert errors == ["USB microphone disconnected"]
    assert backend.stop_calls == 1
    with pytest.raises(RuntimeError, match="while error"):
        controller.start()
    assert controller.reset_error()
    assert controller.state == VoiceoverCaptureState.IDLE
    assert controller.frame_count == controller.duration_ms == 0


def test_start_failures_enter_error_without_leaking_a_take():
    no_device = VoiceoverCaptureController(FakeBackend(devices=()))
    assert not no_device.start()
    assert no_device.state == VoiceoverCaptureState.ERROR
    assert "No microphone" in no_device.error_message

    backend = FakeBackend(start_error=OSError("device busy"))
    busy = VoiceoverCaptureController(backend)
    assert not busy.start()
    assert busy.state == VoiceoverCaptureState.ERROR
    assert busy.error_message == "device busy"
    # _enter_error always asks the backend to release any partial start.
    assert backend.stop_calls == 1


def test_recording_state_is_stable_before_a_synchronous_stop_slot():
    backend = FakeBackend(start_chunk=_pcm(10, 20, 30))
    controller = VoiceoverCaptureController(backend)
    states = []
    finished = []

    def on_state(state):
        states.append(state)
        if state == VoiceoverCaptureState.RECORDING:
            assert backend.active
            assert controller.frame_count == 3
            controller.stop()

    controller.state_changed.connect(on_state)
    controller.recording_finished.connect(finished.append)

    assert not controller.start()
    assert not backend.active
    assert controller.state == VoiceoverCaptureState.IDLE
    assert states == [
        VoiceoverCaptureState.RECORDING,
        VoiceoverCaptureState.FINALIZING,
        VoiceoverCaptureState.IDLE,
    ]
    assert finished[0].frame_count == 3


def test_start_cannot_publish_recording_after_reentrant_close():
    backend = FakeBackend()
    controller = VoiceoverCaptureController(backend)
    original_start = backend.start

    def close_during_start(device, *, on_pcm, on_error):
        original_start(device, on_pcm=on_pcm, on_error=on_error)
        assert backend.active
        controller.close()

    backend.start = close_during_start

    assert not controller.start()
    assert controller.state == VoiceoverCaptureState.IDLE
    assert not backend.active
    with pytest.raises(RuntimeError, match="closed"):
        controller.start()


def test_finalizing_state_allows_synchronous_close_without_finalizing():
    backend = FakeBackend()
    controller = VoiceoverCaptureController(backend)
    finished = []
    controller.recording_finished.connect(finished.append)

    def on_state(state):
        if state == VoiceoverCaptureState.FINALIZING:
            assert not backend.active
            controller.close()

    controller.state_changed.connect(on_state)
    assert controller.start()
    backend.emit(_pcm(1, 2, 3))

    assert controller.stop() is None
    assert controller.state == VoiceoverCaptureState.IDLE
    assert not backend.active
    assert finished == []
    with pytest.raises(RuntimeError, match="closed"):
        controller.start()


def test_error_state_allows_synchronous_reset_and_restart():
    backend = FakeBackend(start_error=OSError("device busy"))
    controller = VoiceoverCaptureController(backend)
    states = []
    restarted = []

    def on_state(state):
        states.append(state)
        if state == VoiceoverCaptureState.ERROR:
            assert not backend.active
            backend.start_error = None
            assert controller.reset_error()
            restarted.append(controller.start())

    controller.state_changed.connect(on_state)

    assert not controller.start()
    assert restarted == [True]
    assert controller.state == VoiceoverCaptureState.RECORDING
    assert backend.active
    assert states == [
        VoiceoverCaptureState.ERROR,
        VoiceoverCaptureState.IDLE,
        VoiceoverCaptureState.RECORDING,
    ]
    controller.cancel()


def test_final_drain_error_discards_incomplete_take_instead_of_finalizing():
    backend = FakeBackend(
        final_chunk=_pcm(4, 5),
        final_error="microphone read failed during final drain",
        stop_error=OSError("secondary stop failure"),
    )
    controller = VoiceoverCaptureController(backend)
    finished = []
    controller.recording_finished.connect(finished.append)

    assert controller.start()
    backend.emit(_pcm(1, 2, 3))

    assert controller.stop() is None
    assert controller.state == VoiceoverCaptureState.ERROR
    assert controller.error_message == \
        "microphone read failed during final drain"
    assert not backend.active
    assert finished == []


def test_event_stamp_synchronously_drains_pcm_before_reading_audio_clock():
    backend = FakeBackend()
    controller = VoiceoverCaptureController(backend)
    assert controller.start()

    backend.queue(_pcm(*range(7)))
    position = controller.append_source_position_ms(12_500)
    backend.queue(_pcm(7, 8, 9))
    marks = [{
        "id": "line-1",
        "kind": "line",
        "ink": "cyan",
        "points": [[0.1, 0.2], [0.3, 0.4]],
        "width": 1.0,
    }]
    strokes = controller.append_telestration_snapshot(marks)

    assert position.audio_frame == 7
    assert strokes.audio_frame == 10
    assert backend.drain_calls == 2
    controller.cancel()


def test_event_clock_drain_error_aborts_recording_without_an_event():
    backend = FakeBackend(drain_error=OSError("input read failed"))
    controller = VoiceoverCaptureController(backend)
    assert controller.start()

    with pytest.raises(RuntimeError, match="input read failed"):
        controller.append_source_position_ms(12_500)

    assert controller.state == VoiceoverCaptureState.ERROR
    assert not backend.active
    assert controller.stop() is None


def test_presentation_events_use_audio_frames_and_same_frame_sequence():
    backend = FakeBackend()
    controller = VoiceoverCaptureController(backend)
    assert controller.start()

    backend.emit(_pcm(*range(10)))
    position = controller.append_source_position_ms(
        50_000, transition=SourcePositionTransition.HARD_SEEK)
    original_marks = [{
        "id": "arrow-1",
        "kind": "arrow",
        "ink": "gold",
        "points": [[0.1, 0.2], [0.7, 0.8]],
        "width": 1.0,
    }]
    strokes = controller.append_telestration_snapshot(original_marks)
    # Mutating the adapter-owned structure cannot mutate the event.
    original_marks[0]["points"][0][0] = 0.99
    backend.emit(_pcm(*range(10, 25)))
    later = controller.append_source_position_ms(50_500)
    captured = controller.stop()

    assert captured is not None
    track = captured.presentation_track
    assert track is not None
    assert track.sample_rate == 48_000
    assert [event.audio_frame for event in track.events] == [10, 10, 25]
    assert [event.sequence for event in track.events] == [0, 1, 2]
    assert position.payload == {
        "source_position_ms": 50_000,
        "transition": "hard_seek",
    }
    assert strokes.payload["marks"][0]["points"][0][0] == 0.1
    assert later.payload == {
        "source_position_ms": 50_500,
        "transition": "continuous",
    }

    encoded = track.to_json()
    assert PresentationEventTrack.from_json(encoded) == track
    assert PresentationEventTrack.from_json(encoded).to_json() == encoded


def test_initial_source_position_precedes_synchronous_start_audio():
    backend = FakeBackend(start_chunk=_pcm(*range(12)))
    controller = VoiceoverCaptureController(backend)
    marks = [{
        "id": "initial-line",
        "kind": "line",
        "ink": "cyan",
        "width": 1.0,
        "points": [[0.1, 0.2], [0.8, 0.7]],
    }]

    assert controller.start(
        initial_source_position_ms=43_210,
        initial_telestration_snapshot=marks,
    )
    captured = controller.stop()

    assert captured is not None
    track = captured.presentation_track
    assert track is not None
    assert track.events[0].audio_frame == 0
    assert track.events[0].sequence == 0
    assert track.events[0].kind == PresentationEventKind.SOURCE_POSITION
    assert track.events[0].payload == {
        "source_position_ms": 43_210,
        "transition": "anchor",
    }
    assert track.events[1].audio_frame == 0
    assert track.events[1].sequence == 1
    assert track.events[1].kind == \
        PresentationEventKind.TELESTRATION_SNAPSHOT
    assert track.events[1].payload == {"marks": marks}
    assert captured.frame_count == 12


@pytest.mark.parametrize("position", [-1, 1.5, True])
def test_initial_source_position_requires_non_negative_integer(position):
    controller = VoiceoverCaptureController(FakeBackend())

    with pytest.raises(ValueError, match="non-negative integer"):
        controller.start(initial_source_position_ms=position)

    assert controller.state == VoiceoverCaptureState.IDLE


def test_initial_telestration_snapshot_is_validated_before_capture_starts():
    backend = FakeBackend()
    controller = VoiceoverCaptureController(backend)

    with pytest.raises(ValueError, match="unknown or missing"):
        controller.start(initial_telestration_snapshot=[{"not": "a mark"}])

    assert controller.state == VoiceoverCaptureState.IDLE
    assert backend.start_calls == 0


def test_presentation_events_require_recording_and_valid_inputs():
    controller = VoiceoverCaptureController(FakeBackend())
    with pytest.raises(RuntimeError, match="active Voiceover"):
        controller.append_source_position_ms(0)
    assert controller.start()
    with pytest.raises(ValueError, match="non-negative integer"):
        controller.append_source_position_ms(-1)
    with pytest.raises(ValueError, match="non-negative integer"):
        controller.append_source_position_ms(1.5)
    with pytest.raises(ValueError, match="transition is invalid"):
        controller.append_source_position_ms(1, transition="invented")
    with pytest.raises(ValueError, match="list of mark objects"):
        controller.append_telestration_snapshot(["not-a-mark"])
    controller.cancel()


def test_source_position_contract_rejects_nominal_frame_payloads():
    with pytest.raises(ValueError, match="cannot use source_frame"):
        PresentationEvent.create(
            audio_frame=0,
            sequence=0,
            kind=PresentationEventKind.SOURCE_POSITION,
            payload={"source_frame": 1_500},
        )
    with pytest.raises(ValueError, match="cannot use source_frame"):
        PresentationEvent.create(
            audio_frame=0,
            sequence=0,
            kind=PresentationEventKind.SOURCE_POSITION,
            payload={
                "source_position_ms": 50_000,
                "source_frame": 1_500,
            },
        )


def test_presentation_events_reject_unknown_immutable_payload_fields():
    with pytest.raises(ValueError, match="unknown or missing fields"):
        PresentationEvent.create(
            audio_frame=0,
            sequence=0,
            kind=PresentationEventKind.SOURCE_POSITION,
            payload={
                "source_position_ms": 50_000,
                "wall_time_ms": 12,
            },
        )
    with pytest.raises(ValueError, match="transition is invalid"):
        PresentationEvent.create(
            audio_frame=0,
            sequence=0,
            kind=PresentationEventKind.SOURCE_POSITION,
            payload={
                "source_position_ms": 50_000,
                "transition": "invented",
            },
        )
    with pytest.raises(ValueError, match="anchor must be at audio frame zero"):
        PresentationEvent.create(
            audio_frame=1,
            sequence=0,
            kind=PresentationEventKind.SOURCE_POSITION,
            payload={
                "source_position_ms": 50_000,
                "transition": "anchor",
            },
        )
    with pytest.raises(ValueError, match="unknown or missing fields"):
        PresentationEvent.create(
            audio_frame=0,
            sequence=0,
            kind=PresentationEventKind.TELESTRATION_SNAPSHOT,
            payload={"marks": [], "selected_tool": "arrow"},
        )


def test_presentation_track_rejects_unknown_track_and_event_fields():
    event = {
        "audio_frame": 0,
        "sequence": 0,
        "kind": "source_position",
        "payload": {"source_position_ms": 50_000},
    }
    with pytest.raises(ValueError, match="unknown or missing fields"):
        PresentationEventTrack.from_json(
            '{"events":[],"sample_rate":48000,"version":1,'
            '"wall_clock":"forbidden"}')
    event["wall_time_ms"] = 1
    with pytest.raises(ValueError, match="unknown or missing fields"):
        PresentationEventTrack.from_json(json.dumps({
            "version": 1,
            "sample_rate": 48_000,
            "events": [event],
        }))
    with pytest.raises(ValueError, match="Unsupported presentation"):
        PresentationEventTrack.from_json(json.dumps({
            "version": 1.0,
            "sample_rate": 48_000,
            "events": [],
        }))


def test_snapshot_contract_matches_accepted_legacy_mark_geometry():
    backend = FakeBackend()
    controller = VoiceoverCaptureController(backend)
    assert controller.start()
    marks = [
        {
            "id": "legacy-freehand",
            "kind": "freehand",
            "ink": "gold",
            "points": [[0.25, 0.75]],
            "width": 1.0,
        },
        {
            "id": "legacy-line",
            "kind": "line",
            "ink": "cyan",
            "points": [[0.1, 0.2], [0.2, 0.3], [0.8, 0.9]],
            "width": 17.0,
        },
    ]

    event = controller.append_telestration_snapshot(marks)
    captured = controller.stop()

    assert event.payload == {"marks": marks}
    assert captured is not None
    assert captured.presentation_track.events[0].payload == {"marks": marks}


@pytest.mark.parametrize("mark", [
    {
        "id": "",
        "kind": "arrow",
        "ink": "gold",
        "points": [[0.1, 0.2], [0.3, 0.4]],
        "width": 1.0,
    },
    {
        # "spotlight" used to stand in for an unknown kind. It is a real tool
        # now, so this needs a name the shape library genuinely does not have.
        "id": "bad-kind",
        "kind": "not_a_real_tool",
        "ink": "gold",
        "points": [[0.1, 0.2], [0.3, 0.4]],
        "width": 1.0,
    },
    {
        # "green" is a selectable ink now; this needs a colour that is not.
        "id": "bad-ink",
        "kind": "line",
        "ink": "chartreuse",
        "points": [[0.1, 0.2], [0.3, 0.4]],
        "width": 1.0,
    },
    {
        "id": "bad-width",
        "kind": "line",
        "ink": "cyan",
        "points": [[0.1, 0.2], [0.3, 0.4]],
        "width": float("inf"),
    },
    {
        "id": "bad-point",
        "kind": "circle",
        "ink": "red",
        "points": [[-0.1, 0.2], [0.3, 0.4]],
        "width": 1.0,
    },
    {
        "id": "unknown-field",
        "kind": "line",
        "ink": "gold",
        "points": [[0.1, 0.2], [0.3, 0.4]],
        "width": 1.0,
        "opacity": 0.5,
    },
])
def test_telestration_event_rejects_noncanonical_marks(mark):
    with pytest.raises(ValueError):
        PresentationEvent.create(
            audio_frame=0,
            sequence=0,
            kind=PresentationEventKind.TELESTRATION_SNAPSHOT,
            payload={"marks": [mark]},
        )


def test_audio_enum_name_reads_across_duplicate_pyside_enum_classes():
    """Qt audio enums must be compared by name, never by identity.

    PySide6 6.11 ships two distinct classes named ``Error``: the one
    ``QAudioSource.error()`` returns is not the one ``QAudio.Error``
    resolves to. Same names, same values, never equal. Comparing them
    directly made ``error() != QAudio.Error.NoError`` true for NoError, so
    every recording reported "The microphone stopped with an error." on its
    first state change and no Voiceover could be captured at all.
    """
    import enum

    from tapesift.services.voiceover_capture import _audio_enum_name

    class ErrorFromOneClass(enum.Enum):
        NoError = 0

    class ErrorFromAnotherClass(enum.Enum):
        NoError = 0

    # This inequality is the whole trap: identical name, identical value.
    assert ErrorFromOneClass.NoError != ErrorFromAnotherClass.NoError
    assert _audio_enum_name(ErrorFromOneClass.NoError) == "NoError"
    assert _audio_enum_name(ErrorFromAnotherClass.NoError) == "NoError"


def test_live_audio_source_reports_no_error_by_name(qapp_guard=None):
    """A real, freshly started QAudioSource must not look like a failure."""
    from PySide6.QtMultimedia import QAudioSource, QMediaDevices

    from tapesift.services.voiceover_capture import (
        _audio_enum_name, canonical_audio_format)

    device = QMediaDevices.defaultAudioInput()
    if device.isNull():
        pytest.skip("no audio input device on this machine")
    source = QAudioSource(device, canonical_audio_format())
    source.start()
    try:
        assert _audio_enum_name(source.error()) == "NoError"
    finally:
        source.stop()
