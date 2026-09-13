"""Deterministic single-frame compositor for TapeSift export treatments.

The composition plan owns geometry and paint order.  This module only turns
that validated contract and immutable payload bytes into pixels.  It is used
by preview today and is intentionally suitable for the later frame-by-frame
export path: it has no widgets, files, playback controls, or FFmpeg calls.

``source_video`` in :class:`CompositionFramePayloads` is one encoded still
frame (PNG/JPEG/WebP), not a path or a video stream.  Telestration coordinates
remain fractions of the *source film* and are mapped only after the plan has
resolved its contain-without-crop rectangle.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from io import BytesIO
import json
import math
from pathlib import Path
import struct
import threading
import wave

from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QIODevice,
    QPointF,
    QRectF,
    QThread,
    Qt,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QGuiApplication,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)

from tapesift.models.composition_plan import (
    CompositionLayer,
    CompositionLayerKind,
    CompositionPlan,
    IdentityAssetRole,
    ImmutableAssetReference,
    LayerFitPolicy,
    LayerMaskPolicy,
    LayerTextSource,
    PixelRect,
)
from tapesift.models.export_package import ExportStyle
from tapesift.models.presentation_track import (
    PresentationEventTrack,
    validate_telestration_snapshot,
)
from tapesift.models.signature_template import (
    ImageAssetSnapshot,
    SignatureTemplateValidationError,
)
from tapesift.models.telestration import INK, MarkKind


__all__ = (
    "CompositionAssetPayload",
    "CompositionFrameCompositor",
    "CompositionFramePayloads",
    "DecodedSourceFrame",
    "PreviewCompositorError",
    "register_compositor_fonts",
)


_BACKGROUND = QColor("#090C0A")
_PRIMARY_TEXT = QColor("#F1F4EE")
_SECONDARY_TEXT = QColor("#9DA69D")
_PROGRESS_TRACK = QColor("#263029")
_MAX_TEXT_LENGTH = 512
_MAX_MARK_WIDTH = 16.0
_FONT_FILES = ("Rajdhani-SemiBold.ttf", "Rajdhani-Bold.ttf")
_WAVEFORM_READ_FRAMES = 8_192
_MARK_CACHE_ENTRIES = 128
_FONT_LOCK = threading.RLock()
_FONT_FAMILY: str | None = None


class PreviewCompositorError(ValueError):
    """A validated composition cannot be rendered from the supplied bytes."""


@dataclass(frozen=True, slots=True)
class CompositionAssetPayload:
    """Immutable bytes for one content-addressed template image."""

    role: IdentityAssetRole
    data: bytes

    def __post_init__(self) -> None:
        try:
            role = IdentityAssetRole(self.role)
        except (TypeError, ValueError) as exc:
            raise PreviewCompositorError("Template asset role is invalid.") from exc
        if not isinstance(self.data, bytes):
            raise PreviewCompositorError(
                f"Template {role.value} data must be immutable bytes."
            )
        object.__setattr__(self, "role", role)


@dataclass(frozen=True, slots=True)
class DecodedSourceFrame:
    """One immutable, tightly packed RGBA source frame from FFmpeg."""

    width: int
    height: int
    rgba: bytes

    def __post_init__(self) -> None:
        for label, value in (("width", self.width), ("height", self.height)):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise PreviewCompositorError(
                    f"Decoded source-frame {label} must be positive."
                )
        if not isinstance(self.rgba, bytes):
            raise PreviewCompositorError(
                "Decoded source-frame pixels must be immutable bytes."
            )
        if len(self.rgba) != self.width * self.height * 4:
            raise PreviewCompositorError(
                "Decoded source-frame RGBA payload has the wrong size."
            )


@dataclass(frozen=True, slots=True)
class CompositionFramePayloads:
    """One frozen set of canonical inputs for a single output frame.

    Text values use strict UTF-8 bytes so a queued job cannot retain mutable
    objects.  ``voiceover_audio`` is a canonical 48 kHz mono PCM16 WAV.
    ``ink_event_track`` is a UTF-8 JSON list in ``marks_to_json`` shape.
    ``presentation_event_track`` is the serialized audio-frame event track.
    """

    source_video: bytes | DecodedSourceFrame
    assets: tuple[CompositionAssetPayload, ...] = ()
    ink_event_track: bytes | None = None
    voiceover_audio: bytes | None = None
    presentation_event_track: bytes | None = None
    voiceover_waveform: tuple[float, ...] | None = None
    voiceover_waveform_rate_hz: int | None = None
    play_call_situation: bytes | None = None
    play_call_concept: bytes | None = None
    play_call_result: bytes | None = None
    timeline_position: int = 0
    timeline_duration: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.source_video, (bytes, DecodedSourceFrame)):
            raise PreviewCompositorError(
                "Source frame must be immutable encoded bytes or decoded RGBA."
            )
        try:
            assets = tuple(self.assets)
        except TypeError as exc:
            raise PreviewCompositorError(
                "Template assets must be an immutable payload sequence."
            ) from exc
        if any(not isinstance(asset, CompositionAssetPayload) for asset in assets):
            raise PreviewCompositorError(
                "Template assets must be CompositionAssetPayload values."
            )
        object.__setattr__(self, "assets", assets)
        for field_name in (
            "ink_event_track",
            "voiceover_audio",
            "presentation_event_track",
            "play_call_situation",
            "play_call_concept",
            "play_call_result",
        ):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, bytes):
                raise PreviewCompositorError(
                    f"{field_name} must be immutable bytes when supplied."
                )
        if self.voiceover_waveform is not None:
            if not isinstance(self.voiceover_waveform, tuple):
                raise PreviewCompositorError(
                    "voiceover_waveform must be an immutable tuple when supplied."
                )
            for value in self.voiceover_waveform:
                if isinstance(value, bool) or not isinstance(value, (int, float)) \
                        or not math.isfinite(float(value)) \
                        or not 0.0 <= float(value) <= 1.0:
                    raise PreviewCompositorError(
                        "voiceover_waveform values must be finite from 0 to 1."
                    )
        if self.voiceover_waveform_rate_hz is not None:
            if self.voiceover_waveform is None:
                raise PreviewCompositorError(
                    "voiceover_waveform_rate_hz requires a waveform payload."
                )
            if isinstance(self.voiceover_waveform_rate_hz, bool) \
                    or not isinstance(self.voiceover_waveform_rate_hz, int) \
                    or self.voiceover_waveform_rate_hz <= 0:
                raise PreviewCompositorError(
                    "voiceover_waveform_rate_hz must be a positive integer."
                )
        if (
            isinstance(self.timeline_position, bool)
            or not isinstance(self.timeline_position, int)
            or self.timeline_position < 0
        ):
            raise PreviewCompositorError(
                "Timeline position must be a non-negative integer."
            )
        if (
            isinstance(self.timeline_duration, bool)
            or not isinstance(self.timeline_duration, int)
            or self.timeline_duration <= 0
        ):
            raise PreviewCompositorError(
                "Timeline duration must be a positive integer."
            )
        if self.timeline_position > self.timeline_duration:
            raise PreviewCompositorError(
                "Timeline position cannot exceed timeline duration."
            )


@dataclass(frozen=True, slots=True)
class _ResolvedFrame:
    source: QImage
    assets: dict[IdentityAssetRole, QImage]
    marks: tuple[dict[str, object], ...]
    waveform: tuple[float, ...]
    waveform_rate_hz: int | None
    texts: dict[LayerTextSource, str]


class CompositionFrameCompositor:
    """Render validated Clean, Signature, and Vertical plans with QPainter."""

    def __init__(self) -> None:
        # Final export reuses one immutable WAV and event track for every
        # output frame. Decoding a long take for each frame would turn a
        # linear render into quadratic work. Keep exactly one identity-keyed
        # envelope per compositor instance; disposing the job compositor
        # releases the potentially large WAV immediately.
        self._waveform_cache: tuple[
            bytes, bytes, tuple[float, ...]
        ] | None = None
        self._asset_cache: tuple[
            CompositionPlan,
            tuple[CompositionAssetPayload, ...],
            dict[IdentityAssetRole, QImage],
        ] | None = None
        self._marks_cache: OrderedDict[
            tuple[bool, bytes | None], tuple[dict[str, object], ...]
        ] = OrderedDict()

    def clear_cache(self) -> None:
        """Release static take bytes held by this compositor instance."""

        self._waveform_cache = None
        self._asset_cache = None
        self._marks_cache.clear()

    def render(
        self,
        plan: CompositionPlan,
        payloads: CompositionFramePayloads,
    ) -> QImage:
        if not isinstance(plan, CompositionPlan):
            raise PreviewCompositorError(
                "A validated CompositionPlan is required."
            )
        plan_errors = plan.validation_errors()
        if plan_errors:
            raise PreviewCompositorError(
                "Composition plan is invalid: " + "; ".join(plan_errors)
            )
        if not isinstance(payloads, CompositionFramePayloads):
            raise PreviewCompositorError(
                "CompositionFramePayloads are required."
            )

        resolved = self._resolve_payloads(plan, payloads)
        output = QImage(
            plan.canvas.width,
            plan.canvas.height,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        output.fill(Qt.GlobalColor.transparent)
        painter = QPainter(output)
        if not painter.isActive():
            raise PreviewCompositorError("QPainter could not start the composition.")
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        try:
            for layer in plan.layers:
                self._paint_layer(painter, plan, layer, payloads, resolved)
        finally:
            painter.end()
        return output

    def render_png(
        self,
        plan: CompositionPlan,
        payloads: CompositionFramePayloads,
    ) -> bytes:
        """Return deterministic PNG bytes for a rendered preview frame."""

        image = self.render(plan, payloads)
        encoded = QByteArray()
        buffer = QBuffer(encoded)
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
            raise PreviewCompositorError("Preview PNG buffer could not be opened.")
        try:
            if not image.save(buffer, "PNG"):
                raise PreviewCompositorError(
                    "Preview frame could not be encoded as PNG."
                )
        finally:
            buffer.close()
        return bytes(encoded)

    def _resolve_payloads(
        self,
        plan: CompositionPlan,
        payloads: CompositionFramePayloads,
    ) -> _ResolvedFrame:
        source = _decode_source_frame(payloads.source_video)
        expected_source = plan.source_film.source_size
        if (source.width(), source.height()) != (
            expected_source.width,
            expected_source.height,
        ):
            raise PreviewCompositorError(
                "Source frame dimensions do not match the composition plan."
            )

        assets = self._resolve_assets(plan, payloads.assets)
        has_ink = plan.layer(CompositionLayerKind.INK) is not None
        marks = self._resolve_marks(
            payloads.ink_event_track, required=has_ink
        )

        has_waveform = plan.layer(CompositionLayerKind.WAVEFORM) is not None
        waveform = self._resolve_waveform(
            payloads.voiceover_audio,
            payloads.presentation_event_track,
            payloads.voiceover_waveform,
            payloads.voiceover_waveform_rate_hz,
            required=has_waveform,
        )

        texts: dict[LayerTextSource, str] = {}
        text_payloads = (
            (
                CompositionLayerKind.SITUATION,
                LayerTextSource.PLAY_CALL_SITUATION,
                "play_call_situation",
                payloads.play_call_situation,
            ),
            (
                CompositionLayerKind.PLAY_CALL,
                LayerTextSource.PLAY_CALL_CONCEPT,
                "play_call_concept",
                payloads.play_call_concept,
            ),
            (
                CompositionLayerKind.RESULT,
                LayerTextSource.PLAY_CALL_RESULT,
                "play_call_result",
                payloads.play_call_result,
            ),
        )
        for kind, text_source, label, raw_value in text_payloads:
            required = plan.layer(kind) is not None
            if required:
                texts[text_source] = _decode_text(raw_value, label)
            elif raw_value is not None:
                raise PreviewCompositorError(
                    f"Unexpected {label} payload for a plan without that layer."
                )

        identity = plan.template_identity
        if identity is not None:
            texts[LayerTextSource.TEMPLATE_DISPLAY] = identity.display_text
            texts[LayerTextSource.TEMPLATE_USERNAME] = identity.username_text
            texts[LayerTextSource.TEMPLATE_WORDMARK] = identity.wordmark_text

        return _ResolvedFrame(
            source=source,
            assets=assets,
            marks=marks,
            waveform=waveform,
            waveform_rate_hz=payloads.voiceover_waveform_rate_hz,
            texts=texts,
        )

    def _resolve_assets(
        self,
        plan: CompositionPlan,
        payloads: tuple[CompositionAssetPayload, ...],
    ) -> dict[IdentityAssetRole, QImage]:
        cached = self._asset_cache
        if cached is not None and cached[0] == plan and cached[1] == payloads:
            return cached[2]
        images = _resolve_assets(plan, payloads)
        self._asset_cache = (plan, payloads, images)
        return images

    def _resolve_marks(
        self,
        data: bytes | None,
        *,
        required: bool,
    ) -> tuple[dict[str, object], ...]:
        key = (required, data)
        cached = self._marks_cache.get(key)
        if cached is not None:
            self._marks_cache.move_to_end(key)
            return cached
        marks = _decode_marks(data, required=required)
        self._marks_cache[key] = marks
        self._marks_cache.move_to_end(key)
        while len(self._marks_cache) > _MARK_CACHE_ENTRIES:
            self._marks_cache.popitem(last=False)
        return marks

    def _resolve_waveform(
        self,
        wav_data: bytes | None,
        event_track_data: bytes | None,
        preview_envelope: tuple[float, ...] | None,
        waveform_rate_hz: int | None,
        *,
        required: bool,
    ) -> tuple[float, ...]:
        if preview_envelope is not None:
            if not required:
                raise PreviewCompositorError(
                    "Unexpected voiceover_waveform for a plan without a "
                    "waveform layer."
                )
            if wav_data is not None or event_track_data is not None:
                raise PreviewCompositorError(
                    "Voiceover preview waveform cannot be combined with full "
                    "Voiceover audio inputs."
                )
            return (
                tuple(float(value) for value in preview_envelope)
                if waveform_rate_hz is not None
                else _downsample_preview_waveform(preview_envelope)
            )
        if not required:
            # Preserve strict stale-payload rejection for Clean/non-Voiceover
            # plans rather than letting an old cache mask bad inputs.
            return _decode_waveform(
                wav_data, event_track_data, required=False
            )
        if wav_data is None or event_track_data is None:
            return _decode_waveform(
                wav_data, event_track_data, required=True
            )
        cached = self._waveform_cache
        if cached is not None \
                and cached[0] is wav_data \
                and cached[1] is event_track_data:
            return cached[2]
        envelope = _decode_waveform(
            wav_data, event_track_data, required=True
        )
        self._waveform_cache = (wav_data, event_track_data, envelope)
        return envelope

    def _paint_layer(
        self,
        painter: QPainter,
        plan: CompositionPlan,
        layer: CompositionLayer,
        payloads: CompositionFramePayloads,
        resolved: _ResolvedFrame,
    ) -> None:
        if layer.kind is CompositionLayerKind.BACKGROUND:
            painter.fillRect(_qrect(layer.rect), _BACKGROUND)
            return
        if layer.kind is CompositionLayerKind.FILM:
            painter.fillRect(
                _qrect(plan.source_film.slot_rect),
                QColor(plan.source_film.letterbox_color),
            )
            painter.drawImage(
                _qrect(layer.rect),
                resolved.source,
                QRectF(
                    0.0,
                    0.0,
                    float(resolved.source.width()),
                    float(resolved.source.height()),
                ),
            )
            return
        if layer.kind is CompositionLayerKind.INK:
            _paint_marks(painter, layer.rect, resolved.marks)
            return
        if layer.kind is CompositionLayerKind.BORDER:
            # The plan currently rejects this layer.  Keeping the branch an
            # explicit failure prevents a future relaxed plan from silently
            # inventing stretch/crop semantics here.
            raise PreviewCompositorError(
                "Border rendering is undefined for linked Signature/Vertical plans."
            )
        if layer.kind in {
            CompositionLayerKind.PROFILE_PHOTO,
            CompositionLayerKind.WORDMARK,
        } and layer.asset is not None:
            image = resolved.assets[layer.asset.role]
            _paint_asset(painter, layer, image)
            return
        if layer.kind in {
            CompositionLayerKind.IDENTITY_DISPLAY,
            CompositionLayerKind.IDENTITY_USERNAME,
            CompositionLayerKind.WORDMARK,
            CompositionLayerKind.SITUATION,
            CompositionLayerKind.PLAY_CALL,
            CompositionLayerKind.RESULT,
        }:
            text = resolved.texts.get(layer.text_source, "")
            _paint_text(painter, layer, text, plan)
            return
        if layer.kind is CompositionLayerKind.WAVEFORM:
            _paint_waveform(
                painter,
                layer.rect,
                resolved.waveform,
                QColor(plan.template_identity.accent_color),
                timeline_position=payloads.timeline_position,
                timeline_duration=payloads.timeline_duration,
                moving=resolved.waveform_rate_hz is not None,
            )
            return
        if layer.kind is CompositionLayerKind.PROGRESS:
            _paint_progress(
                painter,
                layer.rect,
                payloads.timeline_position,
                payloads.timeline_duration,
                QColor(plan.template_identity.accent_color),
            )
            return
        raise PreviewCompositorError(
            f"Unsupported composition layer: {layer.kind.value}."
        )


def _qrect(rect: PixelRect) -> QRectF:
    return QRectF(float(rect.x), float(rect.y), float(rect.width), float(rect.height))


def _decode_source_frame(data: bytes | DecodedSourceFrame) -> QImage:
    if isinstance(data, DecodedSourceFrame):
        image = QImage(
            data.rgba,
            data.width,
            data.height,
            data.width * 4,
            QImage.Format.Format_RGBA8888,
        ).copy()
        if image.isNull():
            raise PreviewCompositorError(
                "Decoded source-frame RGBA payload could not create an image."
            )
        return image.convertToFormat(
            QImage.Format.Format_ARGB32_Premultiplied
        )
    if not data:
        raise PreviewCompositorError("Source frame bytes are missing.")
    image = QImage.fromData(data)
    if image.isNull():
        raise PreviewCompositorError("Source frame bytes are not a decodable image.")
    return image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


def _resolve_assets(
    plan: CompositionPlan,
    payloads: tuple[CompositionAssetPayload, ...],
) -> dict[IdentityAssetRole, QImage]:
    supplied: dict[IdentityAssetRole, bytes] = {}
    for payload in payloads:
        if payload.role in supplied:
            raise PreviewCompositorError(
                f"Template asset is duplicated: {payload.role.value}."
            )
        supplied[payload.role] = payload.data

    required: dict[IdentityAssetRole, ImmutableAssetReference] = {}
    for layer in plan.layers:
        if layer.asset is not None:
            required[layer.asset.role] = layer.asset
    missing = sorted(role.value for role in required.keys() - supplied.keys())
    if missing:
        raise PreviewCompositorError(
            "Template asset bytes are missing: " + ", ".join(missing) + "."
        )
    unexpected = sorted(role.value for role in supplied.keys() - required.keys())
    if unexpected:
        raise PreviewCompositorError(
            "Template asset bytes are not used by the plan: "
            + ", ".join(unexpected)
            + "."
        )

    images: dict[IdentityAssetRole, QImage] = {}
    for role, reference in required.items():
        data = supplied[role]
        try:
            snapshot = ImageAssetSnapshot(
                data=data,
                sha256=reference.sha256,
                mime_type=reference.mime_type,
                width=reference.width,
                height=reference.height,
            )
        except SignatureTemplateValidationError as exc:
            raise PreviewCompositorError(
                f"Template {role.value} failed MIME/hash validation: {exc}."
            ) from exc
        image = QImage.fromData(snapshot.data)
        if image.isNull():
            raise PreviewCompositorError(
                f"Template {role.value} could not be decoded after validation."
            )
        images[role] = image.convertToFormat(
            QImage.Format.Format_ARGB32_Premultiplied
        )
    return images


def _decode_marks(
    data: bytes | None,
    *,
    required: bool,
) -> tuple[dict[str, object], ...]:
    if not required:
        if data is not None:
            raise PreviewCompositorError(
                "Unexpected ink_event_track for a plan without an ink layer."
            )
        return ()
    if data is None:
        raise PreviewCompositorError("ink_event_track bytes are missing.")
    try:
        decoded = json.loads(data.decode("utf-8"))
        validate_telestration_snapshot(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PreviewCompositorError(
            f"ink_event_track is invalid: {exc}."
        ) from exc
    for mark in decoded:
        width = float(mark["width"])
        if width > _MAX_MARK_WIDTH:
            raise PreviewCompositorError(
                f"ink_event_track mark width exceeds {_MAX_MARK_WIDTH:g}."
            )
    # JSON decoding produced fresh objects and validation has closed every
    # mutability/shape escape.  Freeze the outer sequence for this render.
    return tuple(decoded)


def _decode_waveform(
    wav_data: bytes | None,
    event_track_data: bytes | None,
    *,
    required: bool,
) -> tuple[float, ...]:
    if not required:
        if wav_data is not None or event_track_data is not None:
            raise PreviewCompositorError(
                "Unexpected voiceover payload for a plan without a waveform layer."
            )
        return ()
    if wav_data is None:
        raise PreviewCompositorError("voiceover_audio bytes are missing.")
    if event_track_data is None:
        raise PreviewCompositorError("presentation_event_track bytes are missing.")

    try:
        with wave.open(BytesIO(wav_data), "rb") as reader:
            channels = reader.getnchannels()
            sample_width = reader.getsampwidth()
            sample_rate = reader.getframerate()
            frame_count = reader.getnframes()
            compression = reader.getcomptype()
            waveform = _waveform_envelope_from_reader(reader, frame_count)
            extra = reader.readframes(1)
    except (EOFError, wave.Error) as exc:
        raise PreviewCompositorError(
            f"voiceover_audio is not a valid WAV: {exc}."
        ) from exc
    if (
        channels != 1
        or sample_width != 2
        or sample_rate != 48_000
        or compression != "NONE"
    ):
        raise PreviewCompositorError(
            "voiceover_audio must be 48 kHz mono uncompressed PCM16 WAV."
        )
    if extra:
        raise PreviewCompositorError(
            "voiceover_audio does not contain its declared PCM frame payload."
        )

    try:
        track = PresentationEventTrack.from_json(
            event_track_data.decode("utf-8")
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise PreviewCompositorError(
            f"presentation_event_track is invalid: {exc}."
        ) from exc
    if track.sample_rate != sample_rate:
        raise PreviewCompositorError(
            "presentation_event_track sample rate differs from voiceover audio."
        )
    if track.events and track.events[-1].audio_frame > frame_count:
        raise PreviewCompositorError(
            "presentation_event_track extends beyond voiceover audio."
        )
    return waveform


def _waveform_envelope_from_reader(
        reader: wave.Wave_read, frame_count: int) -> tuple[float, ...]:
    """Build the fixed envelope without duplicating an entire long WAV.

    A 60-minute canonical take is roughly 346 MB. Reading it into a second
    monolithic PCM byte string while the immutable WAV is already resident
    creates an avoidable memory spike. The bucket result is identical while
    reads stay bounded to small chunks.
    """
    if frame_count <= 0:
        return (0.0,) * 64
    bucket_count = min(96, max(16, frame_count))
    sums = [0.0] * bucket_count
    counts = [0] * bucket_count
    index = 0
    while index < frame_count:
        requested = min(_WAVEFORM_READ_FRAMES, frame_count - index)
        pcm = reader.readframes(requested)
        if len(pcm) != requested * 2:
            raise PreviewCompositorError(
                "voiceover_audio does not contain its declared PCM frame payload."
            )
        for sample_offset, (sample,) in enumerate(struct.iter_unpack("<h", pcm)):
            sample_index = index + sample_offset
            bucket = min(
                bucket_count - 1,
                sample_index * bucket_count // frame_count,
            )
            normalized = sample / 32768.0
            sums[bucket] += normalized * normalized
            counts[bucket] += 1
        index += requested
    return tuple(
        math.sqrt(total / count) if count else 0.0
        for total, count in zip(sums, counts)
    )


def _downsample_preview_waveform(
        values: tuple[float, ...]) -> tuple[float, ...]:
    """Collapse the persisted real capture envelope to at most 96 bars."""
    if not values:
        return (0.0,) * 64
    if len(values) <= 96:
        return tuple(float(value) for value in values)
    result: list[float] = []
    for bucket in range(96):
        start = bucket * len(values) // 96
        end = (bucket + 1) * len(values) // 96
        result.append(max(float(value) for value in values[start:end]))
    return tuple(result)


def _waveform_envelope(pcm: bytes, frame_count: int) -> tuple[float, ...]:
    if frame_count <= 0:
        return (0.0,) * 64
    bucket_count = min(96, max(16, frame_count))
    sums = [0.0] * bucket_count
    counts = [0] * bucket_count
    for index, (sample,) in enumerate(struct.iter_unpack("<h", pcm)):
        bucket = min(bucket_count - 1, index * bucket_count // frame_count)
        normalized = sample / 32768.0
        sums[bucket] += normalized * normalized
        counts[bucket] += 1
    return tuple(
        math.sqrt(total / count) if count else 0.0
        for total, count in zip(sums, counts)
    )


def _decode_text(data: bytes | None, label: str) -> str:
    if data is None:
        raise PreviewCompositorError(f"{label} bytes are missing.")
    try:
        value = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PreviewCompositorError(f"{label} is not valid UTF-8.") from exc
    if not value or value != value.strip():
        raise PreviewCompositorError(
            f"{label} must contain canonical text without outer whitespace."
        )
    if len(value) > _MAX_TEXT_LENGTH:
        raise PreviewCompositorError(
            f"{label} cannot exceed {_MAX_TEXT_LENGTH} characters."
        )
    if any(not character.isprintable() for character in value):
        raise PreviewCompositorError(f"{label} must be printable single-line text.")
    return value


def _paint_asset(
    painter: QPainter,
    layer: CompositionLayer,
    image: QImage,
) -> None:
    target = _asset_target_rect(layer.rect, image, layer.fit_policy)
    painter.save()
    try:
        if layer.mask_policy is LayerMaskPolicy.CIRCLE:
            clip = QPainterPath()
            clip.addEllipse(_qrect(layer.rect))
            painter.setClipPath(clip)
        painter.drawImage(
            target,
            image,
            QRectF(0.0, 0.0, float(image.width()), float(image.height())),
        )
    finally:
        painter.restore()


def _asset_target_rect(
    rect: PixelRect,
    image: QImage,
    fit_policy: LayerFitPolicy,
) -> QRectF:
    slot = _qrect(rect)
    if fit_policy is LayerFitPolicy.NONE:
        return slot
    width_scale = rect.width / image.width()
    height_scale = rect.height / image.height()
    scale = (
        max(width_scale, height_scale)
        if fit_policy is LayerFitPolicy.COVER
        else min(width_scale, height_scale)
    )
    width = image.width() * scale
    height = image.height() * scale
    return QRectF(
        rect.x + ((rect.width - width) / 2.0),
        rect.y + ((rect.height - height) / 2.0),
        width,
        height,
    )


def _paint_marks(
    painter: QPainter,
    rect: PixelRect,
    marks: tuple[dict[str, object], ...],
) -> None:
    painter.save()
    painter.setClipRect(_qrect(rect))
    try:
        for mark in marks:
            kind = MarkKind(mark["kind"])
            points = tuple(
                QPointF(
                    rect.x + (float(point[0]) * rect.width),
                    rect.y + (float(point[1]) * rect.height),
                )
                for point in mark["points"]
            )
            color = QColor(INK[mark["ink"]])
            multiplier = float(mark["width"])
            core_width = max(3.6, rect.width / 240.0) * multiplier
            for pen_color, pen_width in (
                (QColor(0, 0, 0, 165), core_width * 2.5),
                (color, core_width),
            ):
                pen = QPen(pen_color, pen_width)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                if kind is MarkKind.CIRCLE:
                    painter.drawEllipse(QRectF(points[0], points[-1]).normalized())
                elif kind is MarkKind.FREEHAND:
                    if len(points) == 1:
                        painter.drawPoint(points[0])
                    else:
                        painter.drawPolyline(QPolygonF(points))
                else:
                    painter.drawLine(points[0], points[-1])
                    if kind is MarkKind.ARROW:
                        _paint_arrow_head(
                            painter,
                            points[0],
                            points[-1],
                            pen_color,
                            pen_width,
                        )
    finally:
        painter.restore()


def _paint_arrow_head(
    painter: QPainter,
    start: QPointF,
    end: QPointF,
    color: QColor,
    width: float,
) -> None:
    angle = math.atan2(end.y() - start.y(), end.x() - start.x())
    size = max(11.0, width * 3.4)
    spread = math.radians(26.0)
    head = QPolygonF((
        end,
        QPointF(
            end.x() - size * math.cos(angle - spread),
            end.y() - size * math.sin(angle - spread),
        ),
        QPointF(
            end.x() - size * math.cos(angle + spread),
            end.y() - size * math.sin(angle + spread),
        ),
    ))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawPolygon(head)
    painter.setBrush(Qt.BrushStyle.NoBrush)


def _paint_text(
    painter: QPainter,
    layer: CompositionLayer,
    text: str,
    plan: CompositionPlan,
) -> None:
    if not text:
        return
    color = _PRIMARY_TEXT
    weight = QFont.Weight.DemiBold
    maximum = max(10.0, layer.rect.height * 0.72)
    minimum = max(7.0, min(14.0, layer.rect.height * 0.3))
    alignment = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter

    if layer.kind in {
        CompositionLayerKind.IDENTITY_USERNAME,
        CompositionLayerKind.SITUATION,
    }:
        color = _SECONDARY_TEXT
        weight = QFont.Weight.Medium
        maximum = max(8.0, layer.rect.height * 0.62)
    elif layer.kind is CompositionLayerKind.WORDMARK:
        color = QColor(plan.template_identity.accent_color)
        weight = QFont.Weight.Bold
    elif layer.kind is CompositionLayerKind.PLAY_CALL:
        weight = QFont.Weight.Bold
        maximum = max(12.0, layer.rect.height * 0.68)
    elif layer.kind is CompositionLayerKind.RESULT:
        color = QColor(plan.template_identity.accent_color)
        weight = QFont.Weight.Bold
        alignment = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter

    font = _fit_font(text, layer.rect, maximum, minimum, weight)
    painter.save()
    try:
        # A very long call may reach the minimum readable size.  The plan's
        # information region remains a hard boundary even in that case.
        painter.setClipRect(_qrect(layer.rect))
        painter.setFont(font)
        painter.setPen(color)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawText(_qrect(layer.rect), int(alignment), text)
    finally:
        painter.restore()


def _fit_font(
    text: str,
    rect: PixelRect,
    maximum: float,
    minimum: float,
    weight: QFont.Weight,
) -> QFont:
    family = _compositor_font_family()
    point_size = maximum
    while point_size > minimum:
        font = QFont(family)
        font.setWeight(weight)
        font.setPixelSize(max(1, int(round(point_size))))
        metrics = QFontMetricsF(font)
        if (
            metrics.horizontalAdvance(text) <= rect.width
            and metrics.height() <= rect.height
        ):
            return font
        point_size -= 1.0
    font = QFont(family)
    font.setWeight(weight)
    font.setPixelSize(max(1, int(round(minimum))))
    return font


def register_compositor_fonts() -> str:
    """Register TapeSift's bundled display face for portable output pixels.

    Relying on a machine font made headless preview render missing-glyph boxes
    and would let final exports vary by Windows installation.  These are
    shipped application resources, not user-selected or mutable template
    files.
    """

    global _FONT_FAMILY
    with _FONT_LOCK:
        if _FONT_FAMILY is not None:
            return _FONT_FAMILY
    app = QGuiApplication.instance()
    if app is None:
        raise PreviewCompositorError(
            "A QGuiApplication is required to render composited text."
        )
    if QThread.currentThread() != app.thread():
        raise PreviewCompositorError(
            "Bundled compositor fonts must be registered on the GUI thread "
            "before a background export starts."
        )
    with _FONT_LOCK:
        if _FONT_FAMILY is not None:
            return _FONT_FAMILY
        font_dir = Path(__file__).resolve().parent.parent / "resources" / "fonts"
        families: list[str] = []
        for filename in _FONT_FILES:
            font_id = QFontDatabase.addApplicationFont(str(font_dir / filename))
            if font_id >= 0:
                families.extend(QFontDatabase.applicationFontFamilies(font_id))
        if not families:
            raise PreviewCompositorError(
                "Bundled compositor fonts could not be registered."
            )
        _FONT_FAMILY = families[0]
        return _FONT_FAMILY


def _compositor_font_family() -> str:
    with _FONT_LOCK:
        if _FONT_FAMILY is not None:
            return _FONT_FAMILY
    return register_compositor_fonts()


def _paint_waveform(
    painter: QPainter,
    rect: PixelRect,
    envelope: tuple[float, ...],
    accent: QColor,
    *,
    timeline_position: int,
    timeline_duration: int,
    moving: bool,
) -> None:
    painter.save()
    try:
        area = _qrect(rect)
        painter.setClipRect(area)
        center = area.center().y()
        baseline = QColor(accent)
        baseline.setAlpha(70)
        painter.setPen(QPen(baseline, 1.0))
        painter.drawLine(
            QPointF(area.left(), center), QPointF(area.right(), center)
        )
        if not envelope:
            return
        visible = (
            _moving_waveform_window(
                envelope,
                timeline_position,
                timeline_duration,
                max(16, rect.width // 4),
            )
            if moving else envelope
        )
        spacing = rect.width / len(visible)
        bar_width = max(1.0, min(5.0, spacing * 0.55))
        pen = QPen(accent, bar_width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        maximum_height = max(1.0, rect.height * 0.46)
        for index, amplitude in enumerate(visible):
            x = rect.x + ((index + 0.5) * spacing)
            half_height = max(1.0, min(1.0, amplitude) * maximum_height)
            painter.drawLine(
                QPointF(x, center - half_height),
                QPointF(x, center + half_height),
            )
    finally:
        painter.restore()


def _moving_waveform_window(
    envelope: tuple[float, ...],
    position: int,
    duration: int,
    bar_count: int,
) -> tuple[float, ...]:
    if not envelope:
        return (0.0,) * max(1, bar_count)
    count = max(1, bar_count)
    current = min(
        len(envelope) - 1,
        (position * len(envelope)) // max(1, duration),
    )
    start = current - count // 2
    return tuple(
        float(envelope[index]) if 0 <= index < len(envelope) else 0.0
        for index in range(start, start + count)
    )


def _paint_progress(
    painter: QPainter,
    rect: PixelRect,
    position: int,
    duration: int,
    accent: QColor,
) -> None:
    painter.fillRect(_qrect(rect), _PROGRESS_TRACK)
    completed = (rect.width * position + (duration // 2)) // duration
    if completed > 0:
        painter.fillRect(
            QRectF(float(rect.x), float(rect.y), float(completed), float(rect.height)),
            accent,
        )
