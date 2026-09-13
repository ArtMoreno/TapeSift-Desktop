"""Voiceover input-device discovery and streamed microphone capture.

The controller in this module deliberately knows nothing about the video
player.  Recording is an independent input operation: it never starts,
pauses, seeks, or otherwise commands playback.  A later UI integration can
feed source-position and stroke snapshots into the audio-clock event track,
then persist the finished :class:`CapturedAudio` with clip metadata.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol

from PySide6.QtCore import (
    QCoreApplication,
    QMicrophonePermission,
    QObject,
    Qt,
    Signal,
)
from PySide6.QtMultimedia import (
    QAudio,
    QAudioDevice,
    QAudioFormat,
    QAudioSource,
    QMediaDevices,
)

from tapesift.models.presentation_track import (
    PresentationEvent,
    PresentationEventKind,
    PresentationEventTrack,
    SourcePositionTransition,
    validate_telestration_snapshot,
)

from tapesift.services.voiceover_service import (
    AudioLevels,
    CANONICAL_CHANNELS,
    CANONICAL_SAMPLE_RATE,
    PCM16_SAMPLE_WIDTH,
    CapturedAudio,
    PcmCaptureBuffer,
)


class AudioCaptureUnavailable(RuntimeError):
    """The requested microphone or canonical input format is unavailable."""


@dataclass(frozen=True)
class AudioInputDevice:
    """Stable, UI-safe description of one Qt audio input."""

    id: str
    name: str
    is_default: bool = False
    supports_canonical: bool = True


def _audio_enum_name(value: object) -> str:
    """Name of a Qt audio enum member, across PySide6's duplicate classes.

    Qt 6.7 moved this enum, and PySide6 6.11 ships *two distinct* classes
    named ``Error`` (and ``State``): the one ``QAudioSource.error()`` returns
    is not the one ``QAudio.Error`` resolves to. They carry identical names
    and values but compare unequal, so ``source.error() != QAudio.Error.NoError``
    is True even when there is no error — which reported a microphone failure
    on the first state change of every recording.

    Comparing by name is stable whichever class an object came from.
    """
    return getattr(value, "name", None) or str(value).rsplit(".", 1)[-1]


def canonical_audio_format() -> QAudioFormat:
    """Return TapeSift's one persisted Voiceover capture format."""
    audio_format = QAudioFormat()
    audio_format.setSampleRate(CANONICAL_SAMPLE_RATE)
    audio_format.setChannelCount(CANONICAL_CHANNELS)
    audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    return audio_format


def select_audio_input_device(
        devices: Sequence[AudioInputDevice], *, preferred_id: str = "",
        preferred_name: str = "") -> AudioInputDevice:
    """Resolve a persisted preference with deterministic safe fallbacks.

    Device ids are preferred.  The description is the fallback because some
    Windows audio drivers replace their opaque Qt id after an update.  If both
    are stale, TapeSift uses the default canonical device and then the first
    canonical device.  An unsupported input is never silently opened.
    """
    usable = tuple(device for device in devices
                   if device.supports_canonical)
    if not usable:
        if devices:
            raise AudioCaptureUnavailable(
                "No microphone supports 48 kHz mono 16-bit recording.")
        raise AudioCaptureUnavailable("No microphone is available.")

    if preferred_id:
        for device in usable:
            if device.id == preferred_id:
                return device

    normalized_name = preferred_name.strip().casefold()
    if normalized_name:
        for device in usable:
            if device.name.strip().casefold() == normalized_name:
                return device

    for device in usable:
        if device.is_default:
            return device
    return usable[0]


class AudioCaptureBackend(Protocol):
    """Dependency boundary used by the controller and its fake tests."""

    def available_devices(self) -> tuple[AudioInputDevice, ...]: ...

    def start(
            self, device: AudioInputDevice, *,
            on_pcm: Callable[[bytes], None],
            on_error: Callable[[str], None]) -> None: ...

    def drain(self) -> None: ...

    def stop(self) -> None: ...


def _qt_device_id(device: QAudioDevice) -> str:
    """Serialize Qt's opaque QByteArray id without assuming text encoding."""
    return "qt:" + bytes(device.id()).hex()


def _permission_error() -> str | None:
    app = QCoreApplication.instance()
    if app is None or not hasattr(app, "checkPermission"):
        return None
    status = app.checkPermission(QMicrophonePermission())
    if status == Qt.PermissionStatus.Denied:
        return "Microphone permission is denied for TapeSift."
    if status == Qt.PermissionStatus.Undetermined:
        return "Microphone permission has not been granted to TapeSift."
    return None


class QtAudioCaptureBackend(QObject):
    """QAudioSource pull-mode backend for the canonical PCM format.

    ``QAudioSource.start()`` without a sink returns a ``QIODevice``.  TapeSift
    listens to ``readyRead`` and pulls each available PCM chunk immediately;
    it never accumulates a second Qt-side recording buffer.
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._source: QAudioSource | None = None
        self._input = None
        self._on_pcm: Callable[[bytes], None] | None = None
        self._on_error: Callable[[str], None] | None = None
        self._stopping = False
        self._error_reported = False

    @staticmethod
    def _native_inputs() -> tuple[QAudioDevice, ...]:
        return tuple(QMediaDevices.audioInputs())

    def available_devices(self) -> tuple[AudioInputDevice, ...]:
        audio_format = canonical_audio_format()
        default = QMediaDevices.defaultAudioInput()
        default_id = b"" if default.isNull() else bytes(default.id())
        return tuple(
            AudioInputDevice(
                id=_qt_device_id(device),
                name=device.description(),
                is_default=bytes(device.id()) == default_id,
                supports_canonical=device.isFormatSupported(audio_format),
            )
            for device in self._native_inputs()
        )

    def start(
            self, device: AudioInputDevice, *,
            on_pcm: Callable[[bytes], None],
            on_error: Callable[[str], None]) -> None:
        if self._source is not None:
            raise RuntimeError("Audio capture is already active")
        permission_error = _permission_error()
        if permission_error:
            raise AudioCaptureUnavailable(permission_error)

        audio_format = canonical_audio_format()
        native = next(
            (candidate for candidate in self._native_inputs()
             if _qt_device_id(candidate) == device.id),
            None,
        )
        if native is None:
            raise AudioCaptureUnavailable(
                f"The selected microphone is no longer available: "
                f"{device.name}")
        if not native.isFormatSupported(audio_format):
            raise AudioCaptureUnavailable(
                f"{device.name} does not support 48 kHz mono 16-bit input.")

        self._on_pcm = on_pcm
        self._on_error = on_error
        self._error_reported = False
        source = QAudioSource(native, audio_format, self)
        source.stateChanged.connect(self._source_state_changed)
        self._source = source
        try:
            input_device = source.start()
            if self._source is not source:
                raise AudioCaptureUnavailable(
                    "Microphone capture stopped while it was opening.")
            if input_device is None:
                raise AudioCaptureUnavailable(
                    f"Could not open microphone: {device.name}")
            self._input = input_device
            input_device.readyRead.connect(self._drain)
            if _audio_enum_name(source.error()) != "NoError":
                raise AudioCaptureUnavailable(self._source_error_message())
        except Exception:
            try:
                self.stop()
            except Exception:
                pass
            raise

    def stop(self) -> None:
        source = self._source
        input_device = self._input
        if source is None:
            self._input = None
            self._clear_callbacks()
            return
        self._stopping = True
        try:
            try:
                if input_device is not None:
                    # Pull the final Qt chunk before stopping the device.
                    self._drain()
                    try:
                        input_device.readyRead.disconnect(self._drain)
                    except (RuntimeError, TypeError):
                        pass
                source.stop()
            finally:
                try:
                    source.stateChanged.disconnect(self._source_state_changed)
                except (RuntimeError, TypeError):
                    pass
                source.deleteLater()
        finally:
            self._source = None
            self._input = None
            self._stopping = False
            self._clear_callbacks()

    def drain(self) -> None:
        """Synchronously deliver bytes already waiting in the QIODevice."""
        self._drain()

    def _drain(self) -> None:
        input_device = self._input
        callback = self._on_pcm
        if input_device is None or callback is None:
            return
        try:
            data = bytes(input_device.readAll())
        except Exception as exc:
            self._report_error(f"Could not read microphone audio: {exc}")
            return
        if data:
            callback(data)

    def _source_state_changed(self, state: QAudio.State) -> None:
        if self._stopping:
            return
        source = self._source
        if source is None:
            return
        if _audio_enum_name(source.error()) != "NoError":
            self._report_error(self._source_error_message())
        elif _audio_enum_name(state) == "StoppedState":
            self._report_error("Microphone capture stopped unexpectedly.")

    def _source_error_message(self) -> str:
        source = self._source
        error = ("FatalError" if source is None
                 else _audio_enum_name(source.error()))
        messages = {
            "OpenError": "The microphone could not be opened.",
            "IOError": "The microphone reported an input error.",
            "UnderrunError": "The microphone input buffer underrun.",
            "FatalError": "The microphone reported a fatal audio error.",
        }
        return messages.get(error, "The microphone stopped with an error.")

    def _report_error(self, message: str) -> None:
        if self._error_reported:
            return
        self._error_reported = True
        callback = self._on_error
        if callback is not None:
            callback(message)

    def _clear_callbacks(self) -> None:
        self._on_pcm = None
        self._on_error = None


class VoiceoverCaptureState(str, Enum):
    IDLE = "idle"
    RECORDING = "recording"
    FINALIZING = "finalizing"
    ERROR = "error"


@dataclass(frozen=True)
class VoiceoverCapturePolicy:
    """Owner-configurable limits; time is enforced against sample frames."""

    max_duration_ms: int = 60 * 60 * 1000
    waveform_hz: int = 100

    def __post_init__(self) -> None:
        if isinstance(self.max_duration_ms, bool) \
                or not isinstance(self.max_duration_ms, int) \
                or self.max_duration_ms <= 0:
            raise ValueError(
                "Voiceover duration limit must be a positive integer")
        if isinstance(self.waveform_hz, bool) \
                or not isinstance(self.waveform_hz, int) \
                or self.waveform_hz <= 0:
            raise ValueError(
                "Voiceover waveform rate must be a positive integer")


RecorderFactory = Callable[..., PcmCaptureBuffer]


class VoiceoverCaptureController(QObject):
    """State machine coordinating a backend and one streamed WAV take."""

    state_changed = Signal(object)
    devices_changed = Signal(object)
    levels_changed = Signal(float, float)
    progress_changed = Signal(int, int)
    recording_finished = Signal(object)
    recording_cancelled = Signal()
    duration_limit_reached = Signal()
    error_occurred = Signal(str)

    def __init__(
            self, backend: AudioCaptureBackend | None = None, *,
            policy: VoiceoverCapturePolicy | None = None,
            recorder_factory: RecorderFactory = PcmCaptureBuffer,
            parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._backend = backend or QtAudioCaptureBackend(self)
        self._policy = policy or VoiceoverCapturePolicy()
        self._recorder_factory = recorder_factory
        self._state = VoiceoverCaptureState.IDLE
        self._recorder: PcmCaptureBuffer | None = None
        self._selected_device: AudioInputDevice | None = None
        self._levels = AudioLevels()
        self._frame_count = 0
        self._duration_ms = 0
        self._max_frames = 0
        self._backend_active = False
        self._accepting_pcm = False
        self._operation: str | None = None
        self._stopping = False
        self._pending_error: str | None = None
        self._limit_pending = False
        self._limit_announced = False
        self._closed = False
        self._transition_serial = 0
        self._error_message = ""
        self._presentation_events: list[PresentationEvent] = []
        self._next_event_sequence = 0

    @property
    def state(self) -> VoiceoverCaptureState:
        return self._state

    @property
    def selected_device(self) -> AudioInputDevice | None:
        return self._selected_device

    @property
    def levels(self) -> AudioLevels:
        return self._levels

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def duration_ms(self) -> int:
        return self._duration_ms

    @property
    def error_message(self) -> str:
        return self._error_message

    def refresh_devices(self) -> tuple[AudioInputDevice, ...]:
        devices = self._backend.available_devices()
        self.devices_changed.emit(devices)
        return devices

    def start(
            self, *, preferred_device_id: str = "",
            preferred_device_name: str = "",
            initial_source_position_ms: int | None = None,
            initial_telestration_snapshot: list[dict[str, object]] | None = None,
    ) -> bool:
        """Begin microphone capture, publishing RECORDING only when stable."""
        if self._closed:
            raise RuntimeError("Cannot start a closed Voiceover controller")
        if self._state != VoiceoverCaptureState.IDLE:
            raise RuntimeError(
                f"Cannot start Voiceover capture while {self._state.value}")
        if self._operation is not None:
            raise RuntimeError("A Voiceover state transition is in progress")
        if initial_source_position_ms is not None and (
                isinstance(initial_source_position_ms, bool)
                or not isinstance(initial_source_position_ms, int)
                or initial_source_position_ms < 0
        ):
            raise ValueError(
                "Initial source position must be a non-negative integer")
        initial_events: list[PresentationEvent] = []
        if initial_source_position_ms is not None:
            initial_events.append(PresentationEvent.create(
                audio_frame=0,
                sequence=len(initial_events),
                kind=PresentationEventKind.SOURCE_POSITION,
                payload={
                    "source_position_ms": initial_source_position_ms,
                    "transition": SourcePositionTransition.ANCHOR.value,
                },
            ))
        if initial_telestration_snapshot is not None:
            initial_events.append(PresentationEvent.create(
                audio_frame=0,
                sequence=len(initial_events),
                kind=PresentationEventKind.TELESTRATION_SNAPSHOT,
                payload={"marks": initial_telestration_snapshot},
            ))

        self._operation = "starting"
        try:
            device = select_audio_input_device(
                self.refresh_devices(), preferred_id=preferred_device_id,
                preferred_name=preferred_device_name)
            if self._closed:
                self._operation = None
                return False
            recorder = self._recorder_factory(
                sample_rate=CANONICAL_SAMPLE_RATE,
                channels=CANONICAL_CHANNELS,
                waveform_hz=self._policy.waveform_hz,
            )
        except Exception as exc:
            self._operation = None
            self._enter_error(str(exc), stop_backend=False)
            return False

        self._selected_device = device
        self._recorder = recorder
        self._frame_count = 0
        self._duration_ms = 0
        self._levels = AudioLevels()
        self._max_frames = max(
            1,
            CANONICAL_SAMPLE_RATE * self._policy.max_duration_ms // 1000,
        )
        self._accepting_pcm = True
        self._backend_active = False
        self._pending_error = None
        self._limit_pending = False
        self._limit_announced = False
        self._error_message = ""
        self._presentation_events.clear()
        self._next_event_sequence = 0
        # Seed the source position before opening the backend. A real Qt
        # device normally delivers its first readyRead asynchronously, but a
        # backend is allowed to deliver PCM from start() itself. Seeding here
        # guarantees the required frame-zero anchor in both cases.
        self._presentation_events.extend(initial_events)
        self._next_event_sequence = len(initial_events)
        try:
            self._backend.start(
                device, on_pcm=self._accept_pcm,
                on_error=self._backend_error)
            # A custom backend or nested Qt callback can synchronously close
            # the controller while ``start`` is still on the stack.  The
            # backend may nevertheless finish opening after that cleanup, so
            # explicitly tear it down instead of publishing RECORDING from a
            # closed controller.
            if self._closed or self._recorder is not recorder:
                self._accepting_pcm = False
                try:
                    self._backend.stop()
                except Exception:
                    pass
                self._backend_active = False
                try:
                    recorder.cancel()
                except Exception:
                    pass
                self._recorder = None
                self._operation = None
                return False
            self._backend_active = True
            if self._pending_error:
                raise AudioCaptureUnavailable(self._pending_error)
        except Exception as exc:
            message = self._pending_error or str(exc)
            self._operation = None
            self._enter_error(message, stop_backend=True)
            return False

        self._operation = None
        serial = self._publish_state(VoiceoverCaptureState.RECORDING)
        if self._state == VoiceoverCaptureState.RECORDING \
                and self._transition_serial == serial:
            self.levels_changed.emit(self._levels.peak, self._levels.rms)
            if self._state == VoiceoverCaptureState.RECORDING \
                    and self._transition_serial == serial:
                self.progress_changed.emit(
                    self._frame_count, self._duration_ms)
        if self._state == VoiceoverCaptureState.RECORDING \
                and self._limit_pending:
            self._announce_limit_and_stop()
        return self._state == VoiceoverCaptureState.RECORDING

    def stop(self) -> CapturedAudio | None:
        """Stop, finalize one WAV value, and return to idle."""
        if self._state != VoiceoverCaptureState.RECORDING:
            return None
        if self._operation is not None:
            return None
        self._operation = "stopping"
        self._stopping = True
        self._pending_error = None
        try:
            # The real backend drains its QIODevice synchronously here while
            # this controller still accepts recording chunks.
            self._backend.stop()
        except Exception as exc:
            if self._pending_error is None:
                self._pending_error = (
                    f"Could not stop microphone capture: {exc}")
        finally:
            self._backend_active = False
            self._accepting_pcm = False
            self._stopping = False
            self._operation = None
        if self._pending_error:
            self._enter_error(self._pending_error, stop_backend=False)
            return None

        recorder = self._recorder
        if recorder is None:
            self._enter_error(
                "Voiceover recording buffer is missing", stop_backend=False)
            return None

        serial = self._publish_state(VoiceoverCaptureState.FINALIZING)
        # State signals are synchronous. A close() slot is allowed to discard
        # this take; do not resume finalization after that reentrant cleanup.
        if self._state != VoiceoverCaptureState.FINALIZING \
                or self._transition_serial != serial \
                or self._recorder is not recorder or self._closed:
            return None

        self._operation = "finalizing"
        try:
            captured = recorder.finalize()
            captured = replace(
                captured,
                presentation_track=PresentationEventTrack(
                    sample_rate=CANONICAL_SAMPLE_RATE,
                    events=tuple(self._presentation_events),
                ),
            )
        except Exception as exc:
            self._operation = None
            self._enter_error(
                f"Could not finalize Voiceover take: {exc}",
                stop_backend=False,
            )
            return None

        self._operation = None
        self._recorder = None
        self._frame_count = captured.frame_count
        self._duration_ms = captured.duration_ms
        self._state = VoiceoverCaptureState.IDLE
        self._transition_serial += 1
        serial = self._transition_serial
        self.recording_finished.emit(captured)
        if self._state == VoiceoverCaptureState.IDLE \
                and self._transition_serial == serial:
            self.state_changed.emit(VoiceoverCaptureState.IDLE)
        return captured

    def cancel(self) -> bool:
        """Stop and discard an active take, including its temporary file."""
        if self._state != VoiceoverCaptureState.RECORDING:
            return False
        if self._operation is not None:
            return False
        self._operation = "cancelling"
        self._accepting_pcm = False
        recorder = self._recorder
        self._recorder = None
        try:
            self._backend.stop()
        except Exception:
            # Cancellation's guarantee is local cleanup.  A device teardown
            # error must not keep the temporary take alive.
            pass
        self._backend_active = False
        if recorder is not None:
            try:
                recorder.cancel()
            except Exception:
                pass
        self._levels = AudioLevels()
        self._frame_count = 0
        self._duration_ms = 0
        self._presentation_events.clear()
        self._next_event_sequence = 0
        self._pending_error = None
        self._operation = None
        serial = self._publish_state(VoiceoverCaptureState.FINALIZING)
        if self._state != VoiceoverCaptureState.FINALIZING \
                or self._transition_serial != serial or self._closed:
            return True
        serial = self._publish_state(VoiceoverCaptureState.IDLE)
        if self._state == VoiceoverCaptureState.IDLE \
                and self._transition_serial == serial:
            self.levels_changed.emit(0.0, 0.0)
        if self._state == VoiceoverCaptureState.IDLE \
                and self._transition_serial == serial:
            self.progress_changed.emit(0, 0)
        if self._state == VoiceoverCaptureState.IDLE \
                and self._transition_serial == serial:
            self.recording_cancelled.emit()
        return True

    def append_source_position_ms(
            self, source_position_ms: int, *,
            transition: SourcePositionTransition =
            SourcePositionTransition.CONTINUOUS) -> PresentationEvent:
        """Stamp one actually presented VFR-safe source timestamp.

        ``transition`` comes from the transport owner, which alone knows
        whether the painted frame answered a user seek or belongs to normal
        playback/J/K/L.  The microphone controller only stamps that immutable
        fact on its audio-frame clock.
        """
        if isinstance(source_position_ms, bool) \
                or not isinstance(source_position_ms, int) \
                or source_position_ms < 0:
            raise ValueError(
                "Source position must be a non-negative integer")
        try:
            transition = SourcePositionTransition(transition)
        except (TypeError, ValueError) as exc:
            raise ValueError("Source-position transition is invalid") from exc
        self._drain_before_event()
        return self._append_presentation_event(
            PresentationEventKind.SOURCE_POSITION,
            {
                "source_position_ms": source_position_ms,
                "transition": transition.value,
            },
        )

    def append_telestration_snapshot(
            self, marks: list[dict]) -> PresentationEvent:
        """Stamp the complete film-coordinate stroke snapshot supplied to us."""
        validate_telestration_snapshot(marks)
        self._drain_before_event()
        return self._append_presentation_event(
            PresentationEventKind.TELESTRATION_SNAPSHOT,
            {"marks": marks},
        )

    def reset_error(self) -> bool:
        if self._state != VoiceoverCaptureState.ERROR or self._closed:
            return False
        if self._operation is not None:
            return False
        self._error_message = ""
        self._levels = AudioLevels()
        self._frame_count = 0
        self._duration_ms = 0
        self._presentation_events.clear()
        self._next_event_sequence = 0
        self._pending_error = None
        serial = self._publish_state(VoiceoverCaptureState.IDLE)
        if self._state == VoiceoverCaptureState.IDLE \
                and self._transition_serial == serial:
            self.levels_changed.emit(0.0, 0.0)
        if self._state == VoiceoverCaptureState.IDLE \
                and self._transition_serial == serial:
            self.progress_changed.emit(0, 0)
        return True

    def close(self) -> None:
        """Release backend and temporary audio from every public state."""
        if self._closed:
            return
        self._closed = True
        self._accepting_pcm = False
        try:
            self._backend.stop()
        except Exception:
            pass
        self._backend_active = False
        recorder = self._recorder
        self._recorder = None
        if recorder is not None:
            try:
                recorder.cancel()
            except Exception:
                pass
        self._operation = None
        self._pending_error = None
        self._levels = AudioLevels()
        self._frame_count = 0
        self._duration_ms = 0
        self._presentation_events.clear()
        self._next_event_sequence = 0
        if self._state != VoiceoverCaptureState.IDLE:
            self._publish_state(VoiceoverCaptureState.IDLE)

    def _append_presentation_event(
            self, kind: PresentationEventKind,
            payload: dict) -> PresentationEvent:
        if self._state != VoiceoverCaptureState.RECORDING:
            raise RuntimeError(
                "Presentation events require an active Voiceover recording")
        event = PresentationEvent.create(
            audio_frame=self._frame_count,
            sequence=self._next_event_sequence,
            kind=kind,
            payload=payload,
        )
        self._next_event_sequence += 1
        self._presentation_events.append(event)
        return event

    def _drain_before_event(self) -> None:
        """Serialize an event after every PCM byte currently available."""
        if self._state != VoiceoverCaptureState.RECORDING:
            raise RuntimeError(
                "Presentation events require an active Voiceover recording")
        if self._operation is not None:
            raise RuntimeError("Voiceover capture is changing state")
        self._operation = "draining event clock"
        self._pending_error = None
        try:
            self._backend.drain()
        except Exception as exc:
            if self._pending_error is None:
                self._pending_error = f"Could not drain microphone audio: {exc}"
        finally:
            self._operation = None
        if self._pending_error:
            message = self._pending_error
            self._enter_error(message, stop_backend=True)
            raise RuntimeError(message)
        if self._limit_pending \
                and self._state == VoiceoverCaptureState.RECORDING:
            self._announce_limit_and_stop()
        if self._state != VoiceoverCaptureState.RECORDING:
            raise RuntimeError(
                "Voiceover recording ended before the event was stamped")

    def _accept_pcm(self, data: bytes) -> None:
        if not self._accepting_pcm or not data:
            return
        recorder = self._recorder
        if recorder is None:
            self._backend_error("Voiceover recording buffer is missing")
            return

        frame_size = PCM16_SAMPLE_WIDTH * CANONICAL_CHANNELS
        remaining_frames = self._max_frames - recorder.frame_count
        capacity = remaining_frames * frame_size - recorder.pending_byte_count
        accepted = data[:max(0, capacity)]
        try:
            levels = recorder.append(accepted)
        except Exception as exc:
            self._backend_error(f"Could not write microphone audio: {exc}")
            return

        self._levels = levels
        self._frame_count = recorder.frame_count
        self._duration_ms = recorder.duration_ms
        if self._state == VoiceoverCaptureState.RECORDING \
                and self._operation is None:
            serial = self._transition_serial
            self.levels_changed.emit(levels.peak, levels.rms)
            if self._state == VoiceoverCaptureState.RECORDING \
                    and self._transition_serial == serial:
                self.progress_changed.emit(
                    self._frame_count, self._duration_ms)

        if self._frame_count >= self._max_frames:
            self._limit_pending = True
            if self._state == VoiceoverCaptureState.RECORDING \
                    and self._operation is None:
                self._announce_limit_and_stop()

    def _backend_error(self, message: str) -> None:
        message = message or "Voiceover capture failed."
        if self._operation is not None or self._stopping:
            if self._pending_error is None:
                self._pending_error = message
            self._accepting_pcm = False
            return
        if self._state != VoiceoverCaptureState.RECORDING:
            return
        self._enter_error(message, stop_backend=True)

    def _enter_error(self, message: str, *, stop_backend: bool) -> None:
        if self._closed:
            return
        if self._state == VoiceoverCaptureState.ERROR \
                and self._operation is None:
            return
        self._accepting_pcm = False
        self._operation = "error cleanup"
        if stop_backend:
            self._stopping = True
            try:
                self._backend.stop()
            except Exception:
                pass
            finally:
                self._stopping = False
        self._backend_active = False
        recorder = self._recorder
        self._recorder = None
        if recorder is not None:
            try:
                recorder.cancel()
            except Exception:
                pass
        self._levels = AudioLevels()
        self._error_message = message or "Voiceover capture failed."
        self._presentation_events.clear()
        self._next_event_sequence = 0
        self._pending_error = None
        self._operation = None
        serial = self._publish_state(VoiceoverCaptureState.ERROR)
        if self._state == VoiceoverCaptureState.ERROR \
                and self._transition_serial == serial:
            self.levels_changed.emit(0.0, 0.0)
        if self._state == VoiceoverCaptureState.ERROR \
                and self._transition_serial == serial:
            self.error_occurred.emit(self._error_message)

    def _announce_limit_and_stop(self) -> None:
        if self._state != VoiceoverCaptureState.RECORDING:
            return
        self._limit_pending = False
        if not self._limit_announced:
            self._limit_announced = True
            serial = self._transition_serial
            self.duration_limit_reached.emit()
            if self._state != VoiceoverCaptureState.RECORDING \
                    or self._transition_serial != serial:
                return
        self.stop()

    def _publish_state(self, state: VoiceoverCaptureState) -> int:
        if state == self._state:
            return self._transition_serial
        self._state = state
        self._transition_serial += 1
        serial = self._transition_serial
        self.state_changed.emit(state)
        return serial
