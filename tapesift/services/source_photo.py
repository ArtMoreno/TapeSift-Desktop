"""Portable analyst-selected source evidence; no OCR or football inference."""
from __future__ import annotations

import hashlib
import struct
from datetime import datetime, timezone
from pathlib import Path

from tapesift.models.clip import Clip

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def source_identity(path: str) -> dict:
    source = Path(path).resolve(strict=True)
    stat = source.stat()
    if not source.is_file():
        raise ValueError("Choose a local source film.")
    return {"path": str(source), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def validate_photo(photo: dict, data: bytes, clip_id: str) -> str:
    """Reject corrupt or unrelated evidence before decoding its pixels."""
    if not isinstance(photo, dict) or photo.get("version") != 1:
        return "Source photo unavailable: invalid record."
    if photo.get("clip_id") != clip_id:
        return "Source photo unavailable: clip identity does not match."
    integers = ("position_ms", "start_ms", "end_ms", "width", "height")
    if any(type(photo.get(key)) is not int for key in integers):
        return "Source photo unavailable: invalid timing or dimensions."
    if photo["position_ms"] < 0 or not 0 <= photo["start_ms"] < photo["end_ms"]:
        return "Source photo unavailable: invalid timing."
    if not (0 < photo["width"] <= 8192 and 0 < photo["height"] <= 8192):
        return "Source photo unavailable: invalid dimensions."
    for key in ("original_source", "frame_source"):
        source = photo.get(key)
        if not isinstance(source, dict) or not isinstance(source.get("path"), str) or not source["path"]:
            return "Source photo unavailable: invalid source identity."
        if any(type(source.get(k)) is not int or source[k] < 0 for k in ("size", "mtime_ns")):
            return "Source photo unavailable: invalid source identity."
    if not isinstance(data, bytes) or not 24 <= len(data) <= 64 * 1024 * 1024:
        return "Source photo unavailable: missing or oversized image."
    if data[:8] != PNG_SIGNATURE or data[12:16] != b"IHDR":
        return "Source photo unavailable: invalid PNG."
    if struct.unpack(">II", data[16:24]) != (photo["width"], photo["height"]):
        return "Source photo unavailable: image dimensions do not match."
    if hashlib.sha256(data).hexdigest() != photo.get("png_sha256"):
        return "Source photo unavailable: image integrity check failed."
    return ""


def make_photo(clip: Clip, data: bytes, position_ms: int,
               original_source: dict, frame_source: dict) -> dict:
    if len(data) < 24:
        raise ValueError("No source image was captured.")
    width, height = struct.unpack(">II", data[16:24])
    photo = {
        "version": 1, "clip_id": clip.id, "captured_clip_id": clip.id,
        "start_ms": clip.start_ms, "end_ms": clip.end_ms,
        "position_ms": position_ms, "width": width, "height": height,
        "original_source": original_source, "frame_source": frame_source,
        "png_sha256": hashlib.sha256(data).hexdigest(),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    error = validate_photo(photo, data, clip.id)
    if error:
        raise ValueError(error)
    return photo


def photo_warnings(photo: dict, clip: Clip, current_source: str) -> list[str]:
    warnings = []
    if (photo["start_ms"], photo["end_ms"]) != (clip.start_ms, clip.end_ms):
        warnings.append("Clip range changed since capture.")
    try:
        if source_identity(current_source) != photo["original_source"]:
            warnings.append("Original film changed. Saved photo retained.")
    except (OSError, ValueError):
        warnings.append("Original film unavailable. Saved photo retained.")
    return warnings


def time_relationship(position_ms: int, clip: Clip) -> str:
    if position_ms < clip.start_ms:
        return "before clip"
    if position_ms >= clip.end_ms:
        return "after clip"
    return "within clip"
