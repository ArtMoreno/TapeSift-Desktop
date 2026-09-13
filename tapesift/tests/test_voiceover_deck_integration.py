"""Window-owned Voiceover deck wiring and session lifecycle."""

from __future__ import annotations

from array import array
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QCloseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from tapesift.core.config import AppSettings  # noqa: E402
from tapesift.database.voiceover_repository import VoiceoverRepository  # noqa: E402
from tapesift.models.clip import Clip  # noqa: E402
from tapesift.models.presentation_track import PresentationEventKind  # noqa: E402
from tapesift.models.telestration import marks_from_json  # noqa: E402
from tapesift.services.project_service import ProjectSession  # noqa: E402
from tapesift.services.voiceover_capture import (  # noqa: E402
    AudioInputDevice,
    VoiceoverCaptureController,
    VoiceoverCaptureState,
)
from tapesift.ui_core.main_window_workflow import MainWindowWorkflow  # noqa: E402
from tapesift.ui_v2.main_window import MainWindowV2  # noqa: E402


def _pcm(*samples: int) -> bytes:
    return array("h", samples).tobytes()


class FakeBackend:
    def __init__(
            self, *, devices: tuple[AudioInputDevice, ...] | None = None,
            start_chunk: bytes = b"") -> None:
        self.devices = devices if devices is not None else (
            AudioInputDevice("mic-test", "Test microphone", is_default=True),
        )
        self.start_chunk = start_chunk
        self.started_device: AudioInputDevice | None = None
        self.active = False
        self.start_calls = 0
        self.stop_calls = 0
        self.drain_calls = 0
        self.pending = bytearray()
        self._on_pcm = None
        self._on_error = None

    def available_devices(self):
        return self.devices

    def start(self, device, *, on_pcm, on_error):
        self.start_calls += 1
        self.started_device = device
        self._on_pcm = on_pcm
        self._on_error = on_error
        self.active = True
        if self.start_chunk:
            chunk = self.start_chunk
            self.start_chunk = b""
            on_pcm(chunk)

    def drain(self):
        self.drain_calls += 1
        if self.pending and self._on_pcm is not None:
            data = bytes(self.pending)
            self.pending.clear()
            self._on_pcm(data)

    def stop(self):
        self.stop_calls += 1
        self.drain()
        self.active = False

    def queue(self, data: bytes) -> None:
        self.pending.extend(data)

    def emit(self, data: bytes) -> None:
        assert self.active
        assert self._on_pcm is not None
        self._on_pcm(data)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path):
    value = AppSettings(onboarding_seen=True, recent_projects=[])
    value.default_project_folder = str(tmp_path)
    value.default_output_folder = str(tmp_path / "exports")
    value.confirm_before_delete = False
    value.save = lambda *args, **kwargs: None
    return value


@pytest.fixture
def make_window(qapp, settings, monkeypatch):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    windows: list[MainWindowV2] = []

    def build(backend: FakeBackend | None = None):
        backend = backend or FakeBackend()
        controller = VoiceoverCaptureController(backend)
        window = MainWindowV2(
            settings,
            voiceover_capture_controller=controller,
        )
        windows.append(window)
        return window, backend

    yield build

    for window in windows:
        coordinator = getattr(window, "voiceover_deck", None)
        if coordinator is not None:
            coordinator.prepare_session_close()
        if window.session is not None:
            window.session.close()
            window.session = None
        if coordinator is not None:
            coordinator.close()
        window.hide()
        window.deleteLater()
    qapp.processEvents()


def _attach_clip(window: MainWindowV2, tmp_path, name: str = "Voiceover"):
    session = ProjectSession.create(name, tmp_path, tmp_path / "exports")
    clip = session.add_clip(Clip(start_ms=1_000, end_ms=9_000))
    window.session = session
    window._refresh_clip_list()
    assert window.select_clip(
        clip.id,
        seek=False,
        focus_player=False,
        review_autoplay=False,
    )
    # These window tests do not load a decoder. Seed the exact test seam that a
    # real StepVideoWidget paint would populate; the missing-PTS test below
    # deliberately clears it again.
    window.player._last_presented_source_position_ms = clip.start_ms
    window.player._last_presented_selection_epoch = \
        window.player._selection_epoch
    window.player._next_presented_frame_is_hard_seek = False
    return session, clip


def _record_one(window: MainWindowV2, backend: FakeBackend, samples=(1, 2, 3)):
    window.control_center.voiceover_record_button.click()
    assert window.voiceover_capture.state == VoiceoverCaptureState.RECORDING
    backend.emit(_pcm(*samples))
    window.control_center.voiceover_record_button.click()
    assert window.voiceover_capture.state == VoiceoverCaptureState.IDLE


def test_rec_requires_writable_project_and_selected_clip(
        make_window, tmp_path):
    window, _backend = make_window()
    button = window.control_center.voiceover_record_button

    assert window.voiceover_capture.parent() is window
    assert window.voiceover_capture.thread() is window.thread()
    assert not button.isEnabled()
    assert "Open a project" in button.toolTip()

    session = ProjectSession.create(
        "No selected clip", tmp_path, tmp_path / "exports")
    window.session = session
    window._refresh_clip_list()
    window.voiceover_deck.sync()
    assert not button.isEnabled()
    assert "Select one clip" in button.toolTip()


def test_read_only_project_keeps_record_and_clear_disabled(
        make_window, tmp_path):
    writable = ProjectSession.create(
        "Read only voice", tmp_path, tmp_path / "exports")
    clip = writable.add_clip(Clip(start_ms=0, end_ms=1_000))
    path = writable.db_path
    writable.close()

    window, _backend = make_window()
    read_only = ProjectSession.open_read_only(path)
    window.session = read_only
    window._refresh_clip_list()
    assert window.select_clip(
        clip.id, seek=False, focus_player=False, review_autoplay=False)

    assert not window.control_center.voiceover_record_button.isEnabled()
    assert not window.control_center.voice_clear_button.isEnabled()
    assert "read-only" in \
        window.control_center.voiceover_record_button.toolTip()


def test_rec_refuses_until_a_valid_pts_frame_has_been_painted(
        make_window, tmp_path):
    window, backend = make_window()
    _attach_clip(window, tmp_path, "Wait for frame")
    window.player._last_presented_source_position_ms = None

    window.control_center.voiceover_record_button.click()

    assert window.voiceover_capture.state == VoiceoverCaptureState.IDLE
    assert backend.start_calls == 0
    assert window.statusBar().currentMessage() == \
        "Wait for the selected clip's video frame to appear before " \
        "recording Voiceover."


def test_new_clip_selection_refuses_stale_pts_until_target_frame_paints(
        make_window, tmp_path):
    window, backend = make_window()
    session = ProjectSession.create(
        "Selection freshness", tmp_path, tmp_path / "exports")
    first = session.add_clip(Clip(start_ms=1_000, end_ms=2_000))
    second = session.add_clip(Clip(start_ms=3_000, end_ms=4_000))
    window.session = session
    window._refresh_clip_list()
    assert window.select_clip(
        first.id, seek=False, focus_player=False, review_autoplay=False)
    window.player._source_frame_presented(1_500)

    assert window.select_clip(
        second.id, seek=True, focus_player=False, review_autoplay=False)
    window.control_center.voiceover_record_button.click()

    assert window.voiceover_capture.state == VoiceoverCaptureState.IDLE
    assert backend.start_calls == 0

    window.player._source_frame_presented(3_000)
    window.control_center.voiceover_record_button.click()
    assert window.voiceover_capture.state == VoiceoverCaptureState.RECORDING
    assert window.voiceover_deck.recording_context.source_anchor_ms == 3_000
    window.voiceover_capture.cancel()


def test_seek_false_out_of_selected_range_paint_still_refuses_rec(
        make_window, tmp_path):
    window, backend = make_window()
    session = ProjectSession.create(
        "Selection range", tmp_path, tmp_path / "exports")
    first = session.add_clip(Clip(start_ms=1_000, end_ms=2_000))
    second = session.add_clip(Clip(start_ms=3_000, end_ms=4_000))
    window.session = session
    window._refresh_clip_list()
    assert window.select_clip(
        first.id, seek=False, focus_player=False, review_autoplay=False)
    window.player._source_frame_presented(1_500)

    assert window.select_clip(
        second.id, seek=False, focus_player=False, review_autoplay=False)
    window.player._source_frame_presented(1_533)
    window.control_center.voiceover_record_button.click()

    assert window.player.displayed_position_ms() == 1_533
    assert window.voiceover_capture.state == VoiceoverCaptureState.IDLE
    assert backend.start_calls == 0


def test_live_trim_boundary_overrides_stale_timeline_recording_range(
        make_window, tmp_path, monkeypatch):
    window, backend = make_window()
    session, clip = _attach_clip(window, tmp_path, "Trim freshness")
    # Simulate a trim committed after the timeline's cached block range was
    # built. Clip duration uses an end-exclusive [start, end) interval, so the
    # newly trimmed end itself must not be accepted as a recording anchor.
    assert session.trim_clip_boundary(
        clip.id, "end", 8_000, minimum_duration_ms=1) is clip
    monkeypatch.setattr(
        window.player, "recording_anchor_ms", lambda: 8_000)

    window.control_center.voiceover_record_button.click()

    assert window.voiceover_capture.state == VoiceoverCaptureState.IDLE
    assert backend.start_calls == 0
    assert window.statusBar().currentMessage() == \
        "Wait for the selected clip's video frame to appear before " \
        "recording Voiceover."


def test_record_stop_persists_real_meter_audio_clock_and_accepted_events(
        make_window, tmp_path, monkeypatch):
    backend = FakeBackend(start_chunk=_pcm(10, 20, 30, 40))
    window, backend = make_window(backend)
    session, clip = _attach_clip(window, tmp_path, "Event track")
    monkeypatch.setattr(
        window.player, "recording_anchor_ms", lambda: 8_345)
    initial_marks = [{
        "id": "initial-circle",
        "kind": "circle",
        "ink": "gold",
        "width": 1.0,
        "points": [[0.2, 0.2], [0.4, 0.5]],
    }]
    window.player.set_telestration_marks(marks_from_json(initial_marks))

    window.control_center.voiceover_record_button.click()

    assert window.voiceover_capture.state == VoiceoverCaptureState.RECORDING
    assert window.control_center.voiceover_record_button.text() == "■  STOP"
    assert window.voiceover_deck.recording_context.clip_id == clip.id
    backend.emit(_pcm(0, 16_384, -32_768, 4_000))
    assert window.control_center.voiceover_waveform.peak == 1.0
    assert window.control_center.voiceover_waveform.rms > 0.0
    backend.emit((array("h", [1_000]) * 48_000).tobytes())
    assert "00:01" in window.control_center.voiceover_take_label.text()

    backend.queue(_pcm(100, 200, 300))
    # Requested media position is allowed to lead decoder/paint delivery; it
    # must not become a Voiceover event.
    window.player.player.positionChanged.emit(12_600)
    window.player.source_frame_presented.emit(12_590, False)
    marks = [{
        "id": "line-1",
        "kind": "line",
        "ink": "cyan",
        "width": 1.0,
        "points": [[0.1, 0.2], [0.7, 0.8]],
    }]
    backend.queue(_pcm(400, 500))
    window.telestration_edit_accepted.emit(clip.id, marks)
    window.control_center.voiceover_record_button.click()

    take = VoiceoverRepository(session.conn).selected_for_clip(
        session.project.id, clip.id)
    assert take is not None
    assert take.device_id == "mic-test"
    assert take.device_name == "Test microphone"
    assert take.audio_wav.startswith(b"RIFF")
    assert take.frame_count == 48_013
    assert take.presentation_track is not None
    assert [event.kind for event in take.presentation_track.events] == [
        PresentationEventKind.SOURCE_POSITION,
        PresentationEventKind.TELESTRATION_SNAPSHOT,
        PresentationEventKind.SOURCE_POSITION,
        PresentationEventKind.TELESTRATION_SNAPSHOT,
    ]
    assert [event.audio_frame for event in take.presentation_track.events] == [
        0, 0, 48_011, 48_013]
    assert take.presentation_track.events[0].payload == {
        "source_position_ms": 8_345,
        "transition": "anchor",
    }
    assert take.presentation_track.events[1].payload == {
        "marks": initial_marks}
    assert take.presentation_track.events[2].payload == {
        "source_position_ms": 12_590,
        "transition": "continuous",
    }
    assert take.presentation_track.events[3].payload == {"marks": marks}
    assert window.control_center.voiceover_record_button.text() == "●  REC"
    assert window.control_center.voiceover_waveform.waveform == take.waveform
    assert window.control_center.voice_clear_button.isEnabled()
    assert "00:01" in window.control_center.voiceover_take_label.text()


def test_recording_pins_clip_when_selection_changes(
        make_window, tmp_path, monkeypatch):
    window, backend = make_window()
    session = ProjectSession.create(
        "Pinned clip", tmp_path, tmp_path / "exports")
    first = session.add_clip(Clip(start_ms=0, end_ms=2_000))
    second = session.add_clip(Clip(start_ms=3_000, end_ms=5_000))
    window.session = session
    window._refresh_clip_list()
    assert window.select_clip(
        first.id, seek=False, focus_player=False, review_autoplay=False)
    monkeypatch.setattr(window.player, "recording_anchor_ms", lambda: 500)

    window.control_center.voiceover_record_button.click()
    backend.emit(_pcm(10, 20, 30))
    assert window.select_clip(
        second.id, seek=False, focus_player=False, review_autoplay=False)
    window.telestration_edit_accepted.emit(second.id, [])
    window.control_center.voiceover_record_button.click()

    repo = VoiceoverRepository(session.conn)
    assert len(repo.list_summaries_for_clip(session.project.id, first.id)) == 1
    assert repo.list_summaries_for_clip(session.project.id, second.id) == []
    first_take = repo.selected_for_clip(session.project.id, first.id)
    assert first_take is not None
    assert [event.kind for event in first_take.presentation_track.events] == [
        PresentationEventKind.SOURCE_POSITION,
        PresentationEventKind.TELESTRATION_SNAPSHOT,
    ]
    assert not window.control_center.voice_clear_button.isEnabled()


def test_clear_selected_take_promotes_latest_remaining_without_blob_list(
        make_window, tmp_path, monkeypatch):
    window, backend = make_window()
    session, clip = _attach_clip(window, tmp_path, "Clear takes")
    monkeypatch.setattr(
        window.player, "recording_anchor_ms", lambda: 1_500)

    _record_one(window, backend, (100, 200, 300))
    first = VoiceoverRepository(session.conn).selected_for_clip(
        session.project.id, clip.id)
    assert first is not None
    _record_one(window, backend, (400, 500, 600))
    repo = VoiceoverRepository(session.conn)
    second = repo.selected_for_clip(session.project.id, clip.id)
    assert second is not None and second.id != first.id

    monkeypatch.setattr(
        VoiceoverRepository,
        "list_for_clip",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("deck loaded Voiceover BLOBs")),
    )
    window.voiceover_deck.sync()
    window.control_center.voice_clear_button.click()

    summaries = repo.list_summaries_for_clip(session.project.id, clip.id)
    assert [take.id for take in summaries] == [first.id]
    assert summaries[0].selected is True
    assert window.voiceover_deck.selected_take.id == first.id
    assert "TAKE 1" in window.control_center.voiceover_take_label.text()


def test_no_device_is_a_controlled_retryable_deck_state(
        make_window, tmp_path):
    window, _backend = make_window(FakeBackend(devices=()))
    session, clip = _attach_clip(window, tmp_path, "No microphone")

    window.control_center.voiceover_record_button.click()

    assert window.voiceover_capture.state == VoiceoverCaptureState.ERROR
    assert window.control_center.voiceover_record_button.isEnabled()
    assert "No microphone" in \
        window.control_center.voiceover_record_button.toolTip()
    assert VoiceoverRepository(session.conn).list_summaries_for_clip(
        session.project.id, clip.id) == []


def test_project_close_cancels_capture_before_session_connection_closes(
        make_window, tmp_path, monkeypatch):
    window, backend = make_window()
    session, clip = _attach_clip(window, tmp_path, "Close recording")
    path = session.db_path
    monkeypatch.setattr(
        window.player, "recording_anchor_ms", lambda: 1_000)
    window.control_center.voiceover_record_button.click()
    backend.emit(_pcm(1, 2, 3, 4))

    assert window._close_project()

    assert window.session is None
    assert window.voiceover_capture.state == VoiceoverCaptureState.IDLE
    assert not backend.active
    # A stale backend callback is harmless after cancellation.
    backend._on_pcm(_pcm(5, 6, 7))
    reopened = ProjectSession.open(path)
    try:
        assert VoiceoverRepository(reopened.conn).list_summaries_for_clip(
            reopened.project.id, clip.id) == []
    finally:
        reopened.close()


def test_declined_project_close_preserves_active_capture(
        make_window, tmp_path, monkeypatch):
    window, backend = make_window()
    _session, _clip = _attach_clip(window, tmp_path, "Declined close")
    monkeypatch.setattr(
        window.player, "recording_anchor_ms", lambda: 1_000)
    window.control_center.voiceover_record_button.click()
    backend.emit(_pcm(1, 2, 3))

    class RunningExport:
        @staticmethod
        def isRunning():
            return True

    window.export_worker = RunningExport()
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.No,
    )

    assert not window._close_project()
    assert window.session is not None
    assert window.voiceover_capture.state == VoiceoverCaptureState.RECORDING
    assert backend.active
    window.export_worker = None


def test_project_switch_cancels_old_capture_and_never_writes_to_new_session(
        make_window, tmp_path, monkeypatch):
    window, backend = make_window()
    old, clip = _attach_clip(window, tmp_path, "Old project")
    old_path = old.db_path
    incoming = ProjectSession.create(
        "Incoming project", tmp_path, tmp_path / "incoming-exports")
    monkeypatch.setattr(
        window.player, "recording_anchor_ms", lambda: 2_000)
    window.control_center.voiceover_record_button.click()
    backend.emit(_pcm(10, 20, 30))

    assert window._activate_session(incoming)

    assert window.session is incoming
    assert window.voiceover_capture.state == VoiceoverCaptureState.IDLE
    backend._on_pcm(_pcm(40, 50))
    reopened = ProjectSession.open(old_path)
    try:
        assert VoiceoverRepository(reopened.conn).list_summaries_for_clip(
            reopened.project.id, clip.id) == []
    finally:
        reopened.close()
    assert incoming.conn.execute(
        "SELECT COUNT(*) FROM voiceover_takes").fetchone()[0] == 0


def test_app_close_cancels_capture_then_closes_controller(
        make_window, tmp_path, monkeypatch):
    window, backend = make_window()
    _session, _clip = _attach_clip(window, tmp_path, "App close")
    monkeypatch.setattr(
        window.player, "recording_anchor_ms", lambda: 3_000)
    window.control_center.voiceover_record_button.click()
    backend.emit(_pcm(1, 2, 3))
    event = QCloseEvent()

    window.closeEvent(event)

    assert event.isAccepted()
    assert window.session is None
    assert window.voiceover_capture.state == VoiceoverCaptureState.IDLE
    assert not backend.active
    with pytest.raises(RuntimeError, match="closed"):
        window.voiceover_capture.start()
