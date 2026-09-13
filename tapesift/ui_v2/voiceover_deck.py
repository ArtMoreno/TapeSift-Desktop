"""Window-level Voiceover capture bound to the control-center deck.

This coordinator records inputs, never pixels. The microphone controller owns
the audio-frame clock; painted-frame PTS values and accepted clip telestration
snapshots are stamped against that clock and persisted with the WAV take.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject

from tapesift.database.voiceover_repository import VoiceoverRepository
from tapesift.models.presentation_track import SourcePositionTransition
from tapesift.models.telestration import marks_to_json
from tapesift.models.voiceover import VoiceoverTake, VoiceoverTakeSummary
from tapesift.services.voiceover_capture import (
    VoiceoverCaptureController,
    VoiceoverCaptureState,
)

if TYPE_CHECKING:
    from tapesift.services.project_service import ProjectSession
    from tapesift.services.voiceover_service import CapturedAudio
    from tapesift.ui_v2.main_window import MainWindowV2


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _RecordingContext:
    """Immutable identities captured before the microphone is opened."""

    session: ProjectSession
    project_id: int
    clip_id: str
    source_anchor_ms: int
    take_number: int


class VoiceoverDeckCoordinator(QObject):
    """Connect the window-owned capture controller to four deck surfaces."""

    def __init__(
            self, window: MainWindowV2,
            controller: VoiceoverCaptureController) -> None:
        super().__init__(window)
        self.window = window
        self.deck = window.control_center
        self.controller = controller
        self._context: _RecordingContext | None = None
        self._selected_take: VoiceoverTakeSummary | None = None
        self._last_source_position_ms: int | None = None
        self._closed = False

        self.deck.voiceover_record_requested.connect(self.toggle_recording)
        self.deck.voiceover_clear_requested.connect(self.clear_selected_take)
        controller.state_changed.connect(self._state_changed)
        controller.levels_changed.connect(self._levels_changed)
        controller.progress_changed.connect(self._progress_changed)
        controller.recording_finished.connect(self._recording_finished)
        controller.recording_cancelled.connect(self._recording_cancelled)
        controller.duration_limit_reached.connect(self._duration_limit_reached)
        controller.error_occurred.connect(self._capture_error)

        # QMediaPlayer.positionChanged can lead the decoder, most visibly in
        # reverse JKL. Only the PTS of an image StepVideoWidget actually painted
        # is presentation truth. This remains observation-only: Voiceover never
        # commands transport state.
        window.player.source_frame_presented.connect(
            self._source_frame_presented)
        # Only the workflow's accepted persistence boundary is observed.
        # Raw drawing-surface signals include rejected/read-only edits.
        window.telestration_edit_accepted.connect(
            self._telestration_edit_accepted)
        self.sync()

    @property
    def recording_context(self) -> _RecordingContext | None:
        return self._context

    @property
    def selected_take(self) -> VoiceoverTakeSummary | None:
        return self._selected_take

    def sync(self) -> None:
        """Render current capture/selection state without loading audio."""
        if self._closed:
            return
        state = self.controller.state
        if state in (
                VoiceoverCaptureState.RECORDING,
                VoiceoverCaptureState.FINALIZING):
            context = self._context
            take_number = context.take_number if context is not None else None
            self.deck.set_voiceover_recording(
                state == VoiceoverCaptureState.RECORDING,
                busy=state == VoiceoverCaptureState.FINALIZING,
            )
            self.deck.set_voiceover_take(
                take_number=take_number,
                duration_ms=self.controller.duration_ms,
                waveform=(),
                clear_enabled=False,
            )
            self.deck.set_voiceover_available(
                state == VoiceoverCaptureState.RECORDING,
                "Stop and save this Voiceover take"
                if state == VoiceoverCaptureState.RECORDING
                else "Finalizing Voiceover audio…",
            )
            return

        self.deck.set_voiceover_recording(False, busy=False)
        session, clip_id, unavailable = self._writable_selection()
        summaries: list[VoiceoverTakeSummary] = []
        selected: VoiceoverTakeSummary | None = None
        if session is not None and clip_id is not None:
            try:
                summaries = VoiceoverRepository(
                    session.conn).list_summaries_for_clip(
                        session.project.id, clip_id)
                selected = next(
                    (take for take in summaries if take.selected), None)
            except Exception as exc:
                log.exception("Could not list Voiceover take metadata")
                unavailable = "Voiceover take metadata could not be read."
                self._show_status(str(exc) or unavailable)
        self._selected_take = selected
        if selected is None:
            self.deck.set_voiceover_take(
                take_number=(len(summaries) + 1)
                if session is not None and clip_id is not None else None,
                duration_ms=None,
                waveform=(),
                clear_enabled=False,
            )
        else:
            self.deck.set_voiceover_take(
                take_number=summaries.index(selected) + 1,
                duration_ms=selected.duration_ms,
                waveform=selected.waveform,
                clear_enabled=True,
            )

        error = self.controller.error_message \
            if state == VoiceoverCaptureState.ERROR else ""
        enabled = session is not None and clip_id is not None
        self.deck.set_voiceover_available(
            enabled,
            error or unavailable or "Record Voiceover for the selected clip",
        )

    def toggle_recording(self) -> None:
        if self._closed:
            return
        if self.controller.state == VoiceoverCaptureState.RECORDING:
            self.controller.stop()
            return
        if self.controller.state == VoiceoverCaptureState.ERROR:
            self.controller.reset_error()
        if self.controller.state != VoiceoverCaptureState.IDLE:
            return

        session, clip_id, unavailable = self._writable_selection()
        if session is None or clip_id is None:
            self._show_status(unavailable or "Select a clip before recording.")
            self.sync()
            return
        try:
            summaries = VoiceoverRepository(
                session.conn).list_summaries_for_clip(
                    session.project.id, clip_id)
        except Exception as exc:
            log.exception("Could not prepare a Voiceover take")
            self._show_status(
                str(exc) or "Voiceover take metadata could not be read.")
            self.sync()
            return

        source_anchor_ms = self.window.player.recording_anchor_ms()
        selected_clip = session.get_clip(clip_id)
        if source_anchor_ms is None or selected_clip is None or not (
                int(selected_clip.start_ms) <= source_anchor_ms
                < int(selected_clip.end_ms)):
            self._show_status(
                "Wait for the selected clip's video frame to appear before "
                "recording Voiceover.")
            self.sync()
            return
        context = _RecordingContext(
            session=session,
            project_id=int(session.project.id),
            clip_id=clip_id,
            source_anchor_ms=source_anchor_ms,
            take_number=len(summaries) + 1,
        )
        # Publish the pinned context before start(): fake/custom backends may
        # synchronously finish or error while start is still on the stack.
        self._context = context
        self._last_source_position_ms = source_anchor_ms
        started = self.controller.start(
            initial_source_position_ms=source_anchor_ms,
            initial_telestration_snapshot=marks_to_json(
                self.window.player.telestration_marks()),
        )
        if not started and self.controller.state != \
                VoiceoverCaptureState.RECORDING:
            # A duration-limited/reentrant backend may already have emitted a
            # finished take and cleared this exact context.
            if self._context is context:
                self._context = None
                self._last_source_position_ms = None
            self.sync()

    def clear_selected_take(self) -> None:
        if self._closed or self.controller.state != VoiceoverCaptureState.IDLE:
            return
        session, clip_id, unavailable = self._writable_selection()
        selected = self._selected_take
        if session is None or clip_id is None or selected is None:
            self._show_status(unavailable or "There is no selected take to clear.")
            self.sync()
            return
        if selected.project_id != session.project.id \
                or selected.clip_id != clip_id:
            self.sync()
            return
        try:
            deleted = VoiceoverRepository(session.conn).delete(selected.id)
        except Exception as exc:
            log.exception("Could not delete Voiceover take")
            self._show_status(str(exc) or "Could not clear Voiceover take.")
            self.sync()
            return
        if deleted:
            self._show_status("Cleared the selected Voiceover take.")
        self.sync()

    def prepare_session_close(self) -> None:
        """Discard active capture before its pinned SQLite session closes."""
        if self._closed:
            return
        if self.controller.state == VoiceoverCaptureState.RECORDING:
            self.controller.cancel()
        self._context = None
        self._last_source_position_ms = None
        self._selected_take = None
        self.sync()

    def close(self) -> None:
        if self._closed:
            return
        self.prepare_session_close()
        self._closed = True
        self.controller.close()

    def _writable_selection(
            self,
    ) -> tuple[ProjectSession | None, str | None, str]:
        session = getattr(self.window, "session", None)
        if session is None:
            return None, None, "Open a project to record Voiceover."
        if getattr(session, "read_only", False):
            return None, None, "Voiceover is unavailable in read-only mode."
        if int(getattr(session.project, "id", 0) or 0) <= 0:
            return None, None, "Save the project before recording Voiceover."
        clip_id = getattr(self.window, "_selected_clip_id", None)
        if not clip_id or session.get_clip(clip_id) is None:
            return session, None, "Select one clip to record Voiceover."
        return session, str(clip_id), ""

    def _state_changed(self, _state: VoiceoverCaptureState) -> None:
        self.sync()

    def _levels_changed(self, peak: float, rms: float) -> None:
        if self._closed:
            return
        self.deck.set_voiceover_levels(peak, rms)

    def _progress_changed(self, _frames: int, duration_ms: int) -> None:
        if self._closed or self.controller.state != \
                VoiceoverCaptureState.RECORDING:
            return
        context = self._context
        self.deck.set_voiceover_take(
            take_number=context.take_number if context is not None else None,
            duration_ms=duration_ms,
            waveform=None,
            clear_enabled=False,
        )

    def _recording_finished(self, captured: CapturedAudio) -> None:
        context = self._context
        self._context = None
        self._last_source_position_ms = None
        if self._closed or context is None:
            return
        # Project close/switch cancels recording first. This identity guard is
        # the final barrier against a reentrant callback writing to a session
        # that is no longer authoritative.
        if self.window.session is not context.session:
            self._show_status(
                "Voiceover was not saved because the project changed.")
            return
        device = self.controller.selected_device
        take = VoiceoverTake(
            project_id=context.project_id,
            clip_id=context.clip_id,
            label=f"Take {context.take_number}",
            source_anchor_ms=context.source_anchor_ms,
            sample_rate=captured.sample_rate,
            channels=captured.channels,
            frame_count=captured.frame_count,
            duration_ms=captured.duration_ms,
            waveform=captured.waveform,
            presentation_track=captured.presentation_track,
            device_id=device.id if device is not None else "",
            device_name=device.name if device is not None else "",
            audio_wav=captured.wav,
            selected=True,
        )
        try:
            VoiceoverRepository(context.session.conn).save(take)
        except Exception as exc:
            log.exception("Could not persist Voiceover take")
            self._show_status(str(exc) or "Could not save Voiceover take.")
            self.sync()
            return
        self._show_status(
            f"Saved Voiceover Take {context.take_number}.")
        self.sync()

    def _recording_cancelled(self) -> None:
        self._context = None
        self._last_source_position_ms = None
        self.sync()

    def _duration_limit_reached(self) -> None:
        self._show_status(
            "Voiceover reached its recording limit and is being saved.")

    def _capture_error(self, message: str) -> None:
        self._context = None
        self._last_source_position_ms = None
        self._show_status(message or "Voiceover capture failed.")
        self.sync()

    def _source_frame_presented(
            self, source_position_ms: int, hard_seek: bool) -> None:
        if self._closed or self.controller.state != \
                VoiceoverCaptureState.RECORDING:
            return
        context = self._context
        if context is None or self.window.session is not context.session:
            return
        position = max(0, int(source_position_ms))
        if position == self._last_source_position_ms:
            return
        try:
            self.controller.append_source_position_ms(
                position,
                transition=(SourcePositionTransition.HARD_SEEK
                            if hard_seek else
                            SourcePositionTransition.CONTINUOUS),
            )
        except RuntimeError as exc:
            # A backend error or duration limit can end capture while drain()
            # serializes the event. The controller already reports that state.
            if self.controller.state == VoiceoverCaptureState.RECORDING:
                self._show_status(str(exc))
            return
        self._last_source_position_ms = position

    def _telestration_edit_accepted(
            self, clip_id: str, snapshot: object) -> None:
        if self._closed or self.controller.state != \
                VoiceoverCaptureState.RECORDING:
            return
        context = self._context
        if context is None or self.window.session is not context.session \
                or str(clip_id) != context.clip_id:
            return
        if not isinstance(snapshot, list):
            log.warning("Rejected a non-list accepted telestration snapshot")
            return
        try:
            self.controller.append_telestration_snapshot(snapshot)
        except (RuntimeError, ValueError) as exc:
            if self.controller.state == VoiceoverCaptureState.RECORDING:
                self._show_status(str(exc))

    def _show_status(self, message: str) -> None:
        if not self._closed and message:
            self.window.statusBar().showMessage(str(message), 7000)
