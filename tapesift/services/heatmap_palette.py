"""V3 display colors, shared by live logging and saved report projections."""
from __future__ import annotations

import colorsys
import hashlib

from tapesift.services.player_name_service import resolved_key
from tapesift.services.tag_style_service import canonical_tag_key, style_for_tag
from tapesift.services.result_service import legacy_result_key
from tapesift.ui_v2.tag_readout import outcome_key

BACKGROUND = "#080c0a"
EMPTY = "#252e29"
QUARTER_COLORS = {
    "Q1": "#58b397", "Q2": "#589bce", "Q3": "#c5ae64",
    "Q4": "#a185c3", "OT": "#ca7188",
}


def quarter_colour(value: str) -> str:
    key = str(value).strip().upper()
    return QUARTER_COLORS.get(key, QUARTER_COLORS["OT"]
                             if key.endswith("OT") and key[:-2].isdigit() else EMPTY)


def quarter_starts(clips) -> list[tuple[int, str]]:
    """First tagged clip of each period change, not an inferred clock boundary."""
    from tapesift.services.heatmap_service import _quarter_of
    starts = []
    previous = None
    for clip in sorted(clips, key=lambda c: (c.start_ms, c.order_index)):
        quarter = _quarter_of(clip)
        if quarter == "?":
            continue
        if quarter != previous:
            starts.append((clip.start_ms, quarter))
        previous = quarter
    return starts


TYPE_COLORS = {
    "run": "#46976e", "pass": "#5d8eac", "screen": "#9176b2",
    "rpo": "#529d95", "special": "#9984ad", "no_play": "#77817e",
}
RESULT_COLORS = {
    "complete": "#46976e", "first_down": "#91b34e", "score": "#9176b2",
    "turnover": "#bb4653", "negative": "#b37b49", "penalty": "#ad9253",
    "stop": "#77817e", "special": "#9984ad", "other": "#66786e",
}


def result_colour(value: str, styles=None) -> str:
    color = str(style_for_tag(value, styles)["color"])
    if not color:
        key = legacy_result_key(value)
        color = next((str(style_for_tag(name, styles)["color"])
                      for name in (styles or {})
                      if legacy_result_key(name.replace("_", " ")) == key
                      and style_for_tag(name, styles)["color"]), "")
    return color or RESULT_COLORS[outcome_key(value)]


def player_colour(name: str, roster=None) -> str:
    """Identity, not workload or list position, determines a player's color.

    Hashing the normalized name keeps filtering, new clips and restarts from
    recoloring existing players. Names and jersey labels remain visible because
    a full roster inevitably contains similar hues.
    """
    value = str(name or "").strip()
    head, _, tail = value.partition(" ")
    if head.lstrip("#").isdigit() and len(tail.split()) >= 2:
        value = tail
    key = resolved_key(value, roster or {}) if roster else canonical_tag_key(value)
    if not key:
        return EMPTY
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    hue = int.from_bytes(digest[:4], "big") / 2**32
    saturation = .34 + digest[4] / 255 * .18
    lightness = .47 + digest[5] / 255 * .12
    rgb = colorsys.hls_to_rgb(hue, lightness, saturation)
    return "#" + "".join(f"{round(channel * 255):02x}" for channel in rgb)
