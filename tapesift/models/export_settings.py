"""Export presets and settings."""

from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class ExportPreset:
    name: str
    display_name: str
    description: str
    stream_copy: bool = False
    video_codec: str = "libx264"
    crf: int | None = 18
    video_bitrate: str = ""  # e.g. "8M"; empty = use CRF
    encoder_preset: str = "medium"
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"
    pixel_format: str = "yuv420p"
    max_width: int = 0   # 0 = keep source
    max_height: int = 0
    target_width: int = 0   # exact output size (vertical preset)
    target_height: int = 0
    frame_rate: float = 0.0  # 0 = keep source
    center_crop: bool = False
    blurred_background: bool = False
    hardware_encoder: str = ""  # e.g. h264_nvenc; empty = CPU

    def with_hardware(self, encoder: str) -> "ExportPreset":
        return replace(self, hardware_encoder=encoder)


SOURCE_QUALITY = ExportPreset(
    name="source_quality",
    display_name="Source Quality",
    description="Preserve source resolution. H.264 CRF 18, AAC audio.",
)

FAST_COPY = ExportPreset(
    name="fast_copy",
    display_name="Fast Copy",
    description=(
        "Fastest option - copies streams without re-encoding. "
        "Cuts may start a few frames early or late (keyframe boundaries)."
    ),
    stream_copy=True,
)

SOCIAL_1080P = ExportPreset(
    name="social_1080p",
    display_name="Social 1080p",
    description="Scale down to fit 1920x1080, preserve aspect ratio.",
    max_width=1920,
    max_height=1080,
    crf=20,
)

VERTICAL_9_16 = ExportPreset(
    name="vertical_9_16",
    display_name="Vertical 9:16",
    description="1080x1920 center crop for vertical platforms.",
    target_width=1080,
    target_height=1920,
    center_crop=True,
    crf=20,
)


BUILTIN_PRESETS: dict[str, ExportPreset] = {
    p.name: p for p in (SOURCE_QUALITY, FAST_COPY, SOCIAL_1080P, VERTICAL_9_16)
}


def get_preset(name: str) -> ExportPreset:
    return BUILTIN_PRESETS.get(name, SOURCE_QUALITY)


@dataclass
class ReelSettings:
    """Options for combined-reel export."""
    fade_in: bool = False
    fade_out: bool = False
    fade_duration: float = 0.5
    gap_seconds: float = 0.0  # black gap between clips; 0 = hard cuts
    title_card_text: str = ""
    title_card_seconds: float = 3.0
    show_clip_labels: bool = False
