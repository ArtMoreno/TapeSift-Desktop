"""The standard TapeSift identity ships as self-contained vector masters."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from xml.etree import ElementTree

from PySide6.QtGui import QImage


BRAND = Path(__file__).resolve().parents[1] / "resources" / "brand"
ICONS = BRAND.parent / "icons"

REQUIRED_MASTERS = {
    "tapesift-app-icon.svg",
    "tapesift-logo.svg",
    "tapesift-logo-black.svg",
    "tapesift-logo-white.svg",
    "tapesift-mark.svg",
    "tapesift-mark-black.svg",
    "tapesift-mark-micro.svg",
    "tapesift-mark-white.svg",
    "tapesift-wordmark.svg",
    "tapesift-wordmark-black.svg",
    "tapesift-wordmark-white.svg",
    "channels/github/tapesift-github-badge.svg",
}

APPROVED_COLORS = {"#39e07a", "#0a0c0b", "#f2f5f2", "#29312d"}
APP_ICON_SOURCE_SHA256 = (
    "b33313b5485aeaaee7bde71ad253e50b9ec608548cafaf212a8e811414dc4142"
)


def _local_name(element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def test_approved_vector_master_set_is_complete():
    assert {
        path.relative_to(BRAND).as_posix()
        for path in BRAND.rglob("*.svg")
    } == REQUIRED_MASTERS


def test_vector_masters_are_portable_and_use_only_approved_colors():
    forbidden = {"text", "image", "linearGradient", "radialGradient", "filter"}

    for relative in sorted(REQUIRED_MASTERS):
        path = BRAND / relative
        root = ElementTree.parse(path).getroot()
        names = {_local_name(element) for element in root.iter()}
        source = path.read_text(encoding="utf-8")

        assert _local_name(root) == "svg"
        assert root.attrib.get("viewBox")
        assert "path" in names
        assert not names.intersection(forbidden)
        assert "<text" not in source
        assert "font-family" not in source
        assert "data:" not in source
        assert set(re.findall(r"#[0-9A-Fa-f]{6}", source.lower())) \
            <= APPROVED_COLORS


def test_wordmark_and_logo_are_outlined_paths_without_a_font_dependency():
    for name in (
        "tapesift-wordmark.svg",
        "tapesift-wordmark-black.svg",
        "tapesift-wordmark-white.svg",
        "tapesift-logo.svg",
        "tapesift-logo-black.svg",
        "tapesift-logo-white.svg",
    ):
        root = ElementTree.parse(BRAND / name).getroot()
        paths = [
            element for element in root.iter()
            if _local_name(element) == "path"
        ]
        assert paths
        assert all(element.attrib.get("d") for element in paths)


def test_wordmark_masters_describe_the_cut_slits_as_part_of_the_identity():
    for name in (
        "tapesift-wordmark.svg",
        "tapesift-wordmark-black.svg",
        "tapesift-wordmark-white.svg",
    ):
        root = ElementTree.parse(BRAND / name).getroot()
        description = next(
            element for element in root
            if _local_name(element) == "desc"
        )
        assert "edit-cut slits" in (description.text or "")


def test_supplied_application_icon_is_the_canonical_raster_source():
    source = BRAND / "tapesift-app-icon-source.png"
    image = QImage(str(source))

    assert not image.isNull()
    assert (image.width(), image.height()) == (1254, 1254)
    assert hashlib.sha256(source.read_bytes()).hexdigest() \
        == APP_ICON_SOURCE_SHA256


def test_runtime_github_badge_is_a_high_resolution_raster():
    badge = QImage(str(ICONS / "tapesift-github-badge.png"))
    assert not badge.isNull()
    assert (badge.width(), badge.height()) == (128, 128)
