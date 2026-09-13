"""Render TapeSift's canonical brand masters for desktop runtime use.

The outlined wordmark SVGs and supplied application-icon PNG in
``tapesift/resources/brand`` are the source of truth. This script produces the
PNG assets consumed by Qt and the multi-resolution ICO embedded in the Windows
executable without adding Pillow or another runtime dependency.

Usage:
    python scripts/build_brand_assets.py
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QPainterPath
from PySide6.QtSvg import QSvgRenderer


ROOT = Path(__file__).resolve().parent.parent
BRAND = ROOT / "tapesift" / "resources" / "brand"
ICONS = ROOT / "tapesift" / "resources" / "icons"

ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
APP_ICON_SOURCE = BRAND / "tapesift-app-icon-source.png"
APP_ICON_CORNER_RADIUS_RATIO = 0.18


@dataclass(frozen=True)
class RasterExport:
    source: str
    output: str
    width: int
    height: int
    lockup: bool = False


RASTER_EXPORTS = (
    RasterExport(
        "tapesift-app-icon-source.png",
        "tapesift.png",
        1024,
        1024,
    ),
    RasterExport(
        "tapesift-wordmark-white.svg",
        "tapesift-logo.png",
        1600,
        306,
        lockup=True,
    ),
    RasterExport(
        "tapesift-wordmark-black.svg",
        "tapesift-logo-black.png",
        1600,
        306,
        lockup=True,
    ),
    RasterExport(
        "tapesift-wordmark-white.svg",
        "tapesift-logo-white.png",
        1600,
        306,
        lockup=True,
    ),
    RasterExport(
        "tapesift-wordmark.svg", "tapesift-wordmark.png", 1250, 306
    ),
    RasterExport(
        "tapesift-wordmark-black.svg",
        "tapesift-wordmark-black.png",
        1250,
        306,
    ),
    RasterExport(
        "tapesift-wordmark-white.svg",
        "tapesift-wordmark-white.png",
        1250,
        306,
    ),
    RasterExport("tapesift-mark.svg", "tapesift-mark.png", 512, 512),
    RasterExport(
        "tapesift-mark-black.svg", "tapesift-mark-black.png", 512, 512
    ),
    RasterExport(
        "tapesift-mark-white.svg", "tapesift-mark-white.png", 512, 512
    ),
    RasterExport(
        "tapesift-mark-micro.svg", "tapesift-mark-micro.png", 128, 128
    ),
    RasterExport(
        "channels/github/tapesift-github-badge.svg",
        "tapesift-github-badge.png",
        128,
        128,
    ),
)


def _renderer(source: Path | bytes) -> QSvgRenderer:
    if isinstance(source, Path):
        renderer = QSvgRenderer(str(source))
    else:
        renderer = QSvgRenderer(QByteArray(source))
    if not renderer.isValid():
        raise ValueError(f"Invalid SVG source: {source}")
    return renderer


def render_svg(
    source: Path | bytes,
    width: int,
    height: int,
) -> QImage:
    """Render an SVG to a transparent, premultiplied ARGB image."""
    renderer = _renderer(source)
    image = QImage(
        width,
        height,
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    image.fill(Qt.GlobalColor.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.render(painter, QRectF(0, 0, width, height))
    painter.end()
    return image


def render_brand_source(
    source: Path,
    width: int,
    height: int,
) -> QImage:
    """Render either an outlined SVG or the supplied raster icon source."""
    if source.suffix.lower() == ".svg":
        return render_svg(source, width, height)
    image = QImage(str(source))
    if image.isNull():
        raise ValueError(f"Invalid image source: {source}")
    scaled = image.scaled(
        width,
        height,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    if source != APP_ICON_SOURCE:
        return scaled

    # The supplied app-icon render has a black canvas behind its rounded tile.
    # Clip it at export time so Windows receives real transparent corners
    # instead of displaying the canvas as a sharp square.
    output = QImage(
        width,
        height,
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    output.fill(Qt.GlobalColor.transparent)
    radius = min(width, height) * APP_ICON_CORNER_RADIUS_RATIO
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, width, height), radius, radius)

    painter = QPainter(output)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.setClipPath(clip)
    painter.drawImage(QRectF(0, 0, width, height), scaled)
    painter.end()
    return output


def render_lockup(
    wordmark_source: Path,
    width: int,
    height: int,
) -> QImage:
    """Pair the supplied app tile with the cut-slit outlined wordmark."""
    icon_size = height
    gap = 44
    wordmark_width = width - icon_size - gap
    if wordmark_width <= 0:
        raise ValueError("Lockup width must leave room for the wordmark")

    image = QImage(
        width,
        height,
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    image.fill(Qt.GlobalColor.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    icon = render_brand_source(APP_ICON_SOURCE, icon_size, icon_size)
    painter.drawImage(QRectF(0, 0, icon_size, icon_size), icon)
    _renderer(wordmark_source).render(
        painter,
        QRectF(icon_size + gap, 0, wordmark_width, height),
    )
    painter.end()
    return image


def _png_bytes(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise OSError("Could not open an in-memory PNG buffer")
    if not image.save(buffer, "PNG"):
        raise OSError("Qt could not encode a PNG frame")
    buffer.close()
    return bytes(data)


def write_ico(path: Path, frames: list[tuple[int, bytes]]) -> None:
    """Write PNG-compressed frames to a Windows ICO container."""
    header_size = 6 + (16 * len(frames))
    offset = header_size
    entries = bytearray()
    payload = bytearray()

    for size, png in frames:
        dimension = 0 if size == 256 else size
        entries.extend(
            struct.pack(
                "<BBBBHHII",
                dimension,
                dimension,
                0,  # no indexed palette
                0,
                1,  # color planes
                32,
                len(png),
                offset,
            )
        )
        payload.extend(png)
        offset += len(png)

    path.write_bytes(
        struct.pack("<HHH", 0, 1, len(frames)) + entries + payload
    )


def build_assets() -> tuple[Path, ...]:
    """Build all raster runtime assets and return their paths."""
    ICONS.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []

    for export in RASTER_EXPORTS:
        source = BRAND / export.source
        destination = ICONS / export.output
        image = (
            render_lockup(source, export.width, export.height)
            if export.lockup
            else render_brand_source(source, export.width, export.height)
        )
        if not image.save(str(destination), "PNG"):
            raise OSError(f"Qt could not write {destination}")
        outputs.append(destination)

    frames = [
        (
            size,
            _png_bytes(
                render_brand_source(APP_ICON_SOURCE, size, size)
            ),
        )
        for size in ICO_SIZES
    ]
    ico_path = ICONS / "tapesift.ico"
    write_ico(ico_path, frames)
    outputs.append(ico_path)
    return tuple(outputs)


def main() -> int:
    outputs = build_assets()
    for path in outputs:
        print(path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
