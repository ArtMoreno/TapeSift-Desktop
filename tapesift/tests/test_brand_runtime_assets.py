"""Regression coverage for generated desktop brand assets."""

from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtGui import QImage

from scripts.build_brand_assets import ICO_SIZES, RASTER_EXPORTS
from tapesift.app import APP_ICON_PATH


ROOT = Path(__file__).resolve().parents[2]
ICONS = ROOT / "tapesift" / "resources" / "icons"


def test_runtime_rasters_match_the_generation_manifest():
    for export in RASTER_EXPORTS:
        image = QImage(str(ICONS / export.output))
        assert not image.isNull(), export.output
        assert (image.width(), image.height()) == (
            export.width,
            export.height,
        )


def test_generation_manifest_pairs_the_new_icon_with_the_cut_slit_wordmark():
    app_icon = next(
        export for export in RASTER_EXPORTS
        if export.output == "tapesift.png"
    )
    lockup = next(
        export for export in RASTER_EXPORTS
        if export.output == "tapesift-logo.png"
    )

    assert app_icon.source == "tapesift-app-icon-source.png"
    assert not app_icon.lockup
    assert lockup.source == "tapesift-wordmark-white.svg"
    assert lockup.lockup


def test_runtime_application_icon_has_transparent_rounded_corners():
    image = QImage(str(ICONS / "tapesift.png"))

    assert image.hasAlphaChannel()
    assert image.pixelColor(0, 0).alpha() == 0
    assert image.pixelColor(image.width() - 1, 0).alpha() == 0
    assert image.pixelColor(0, image.height() - 1).alpha() == 0
    assert image.pixelColor(
        image.width() - 1,
        image.height() - 1,
    ).alpha() == 0
    assert image.pixelColor(
        image.width() // 2,
        image.height() // 2,
    ).alpha() == 255


def test_windows_icon_contains_every_supported_png_frame():
    payload = APP_ICON_PATH.read_bytes()
    reserved, image_type, count = struct.unpack_from("<HHH", payload)
    assert (reserved, image_type, count) == (0, 1, len(ICO_SIZES))

    dimensions: list[int] = []
    for index in range(count):
        (
            width,
            height,
            _colors,
            _reserved,
            planes,
            bit_depth,
            byte_count,
            offset,
        ) = struct.unpack_from("<BBBBHHII", payload, 6 + index * 16)
        dimensions.append(width or 256)
        assert (height or 256) == dimensions[-1]
        assert (planes, bit_depth) == (1, 32)
        assert payload[offset:offset + min(byte_count, 8)] \
            == b"\x89PNG\r\n\x1a\n"
        frame = QImage.fromData(payload[offset:offset + byte_count], "PNG")
        assert frame.hasAlphaChannel()
        # Tiny frames retain a few alpha levels for a smooth antialiased edge,
        # but the corner must remain visually transparent.
        assert frame.pixelColor(0, 0).alpha() <= 16
        assert frame.pixelColor(
            frame.width() // 2,
            frame.height() // 2,
        ).alpha() == 255

    assert tuple(dimensions) == ICO_SIZES


def test_library_and_standard_launches_share_the_same_shell_icon():
    legacy_source = (ROOT / "tapesift" / "app.py").read_text(
        encoding="utf-8"
    )
    current_source = (ROOT / "tapesift" / "app_v2.py").read_text(
        encoding="utf-8"
    )
    installer = (ROOT / "installer" / "tapesift.iss").read_text(
        encoding="utf-8"
    )

    assert APP_ICON_PATH.name == "tapesift.ico"
    assert "tapesift_library.ico" not in legacy_source
    assert "tapesift_library.ico" not in current_source
    assert "TapeSiftLibrary.ico" not in installer
