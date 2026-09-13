"""Canonical design tokens for the TapeSift V2 interface.

The warm "Precision Soft" palette. theme.py renders its QSS from these
values; QPainter widgets that hardcode these hexes should migrate here as
they are touched, so the whole app restyles from one file.

Radii and control heights are documented scales for new code; the
existing QSS still carries its historical literals for those.
"""

from __future__ import annotations

COLORS = {
    "canvas": "#171815",
    "window": "#1a1b18",
    "surface": "#1f201c",
    "raised": "#25251f",
    "hover": "#2b2b24",
    "line": "#3b3b32",
    "line_strong": "#565345",
    "text": "#f2f5f2",
    "muted": "#9d998b",
    "muted_bright": "#c9c3b3",
    "green": "#39e07a",
    "green_hover": "#62ec98",
    "green_dim": "#25412b",
    "green_dark": "#0f1710",
    # Key relief: the pressed state is one step *down* from the resting
    # surface, and distinctly further from it than hover is. Iteration 2
    # made hover/pressed measurable (they differed by 3px before).
    "pressed": "#191a16",
    # The rate meter's empty track. The spec drawing names #15160f; it
    # resolves here so the meter never carries a bare literal.
    "motion_track": "#15160f",
    # TimelineOverview window chrome. #7ceba4 is a film-map green, not
    # SEMANTIC.commit (#8fd6a8): it marks the visible window on the
    # overview, it does not mean "verified". Named so the overview stops
    # carrying a private hex (ITERATION_2_SPEC item 6). Track stays the
    # historical plate so the overview's contrast against the film cells
    # does not shift; labels keep their muted-green family.
    "overview_track": "#202326",
    "overview_label": "#8d9990",
    "overview_label_dim": "#7f8a82",
    "overview_window": "#7ceba4",
    "overview_dim": "#060806",
    # The app-wide disabled text colour, which theme.py has always carried
    # as a literal. Recorded here so the band's controls resolve from the
    # token table rather than from a second palette - this changes no
    # pixels. It is a COOL grey against a warm chassis, which is the fault
    # C2-03 names: warming it repaints every disabled control in the app,
    # so it belongs to iteration 2's colour pass, not to a structure pass.
    "disabled": "#647169",
    "warning": "#f0c46a",
    "error": "#f17d72",
}

# --- Semantic state colors ("Tungsten") ------------------------------------
#: Colour in the playback surfaces means *state*, never decoration. Five
#: jobs, one hue each, so a colour appearing anywhere tells you something.
#:
#: The accents are deliberately hues that do not occur on a football field.
#: The old green accent shared its hue with the content: against a bright
#: frame of turf it stopped reading as interface, and it spent the one
#: colour that should be free to mean "verified" on ordinary chrome.
#:
#: Position and motion are separated by *temperature* rather than only hue -
#: cold for where you are, warm for what is moving. That survives peripheral
#: vision and colour-blindness far better than two neighbouring hues, which
#: is why the rate readout is always accompanied by text.
#:
#: Added alongside the existing keys rather than replacing them: surfaces
#: migrate to these as they are reworked, so no single change repaints the
#: whole app.
SEMANTIC = {
    # Where you are: playhead, timecode, current play, selection.
    "position": "#6fc6de",
    "position_dim": "#1d4652",
    "position_deep": "#0f2830",
    # What is moving: shuttle rate and direction. Tungsten lamp.
    "motion": "#f0a63c",
    "motion_hi": "#ffc978",
    "motion_dim": "#5a3f14",
    "motion_deep": "#2a1d08",
    # Clip boundaries: IN, OUT, the marked region. Nothing else.
    "marks": "#e8dfc6",
    "marks_dim": "#4a4436",
    # Committing work: save, verified, complete.
    "commit": "#8fd6a8",
    "commit_dim": "#254634",
    # Live capture. Reserved: nothing is this colour until recording.
    "live": "#ff3b30",
    "live_dim": "#4a1512",
}

#: Steps of the shuttle rate meter, dimmest to brightest. The bar lights
#: outward from centre, so distance from rest reads as speed even before
#: the colour does.
MOTION_RAMP = ("#4a3410", "#78521a", "#b07826", "#f0a63c")

#: Corner radius scale (px). Precision surfaces stay near-square; cards,
#: menus and the window shell earn progressively softer corners.
RADIUS_SM = 3
RADIUS_MD = 6
RADIUS_LG = 8
RADIUS_WINDOW = 10

#: Control height scale (px). Compact is the workspace default; default
#: is for dialogs and the start screen.
CONTROL_COMPACT = 22
CONTROL_DEFAULT = 34

# --- Elevation -------------------------------------------------------------
#: Elevation shadow recipes, consumed by elevation.py. Qt stylesheets cannot
#: paint shadows, so in-window depth comes from QGraphicsDropShadowEffect
#: using these values. "card" is for resting raised surfaces (trays, cards);
#: "overlay" is reserved for floating layers that sit above content.
SHADOWS = {
    "card": {"blur": 20, "y": 5, "alpha": 100},
    "overlay": {"blur": 36, "y": 12, "alpha": 130},
}

#: Backdrop scrim color for overlay layers and shadow tint. Warm near-black
#: so shadows stay in the Precision Soft family instead of going blue-grey.
COLORS["backdrop"] = "#0b0c0a"

# --- Wheel W1 material ("Machined"), from 11_SPEC_WHEEL_W1.md -------------
#: The floating wheel's neutral stack, warm-biased to sit with COLORS.canvas.
#: Iteration 2 migrated every wheel literal here so the object can obey
#: SEMANTIC. Values are the spec's exact gradient stops and dash strokes.
WHEEL = {
    # Rim, outside in: warm graphite, light high and slightly left.
    "rim_hi": "#7a7566", "rim_upper": "#403d34",
    "rim_mid": "#26261f", "rim_deep": "#0c0d0a",
    # Dish under the rim.
    "dish_hi": "#33342a", "dish_mid": "#1e1f19", "dish_deep": "#0f100c",
    "dish_edge": "#4a473c",
    # Hub cap, matte.
    "hub_hi": "#2c2d24", "hub_mid": "#181913", "hub_deep": "#0a0b08",
    "hub_edge": "#3a382f",
    # Knurl flutes: dark cut and lit crest, dashed.
    "flute_cut": "#0b0c09", "flute_crest": "#8b8574",
    # Rim catch-light and contact shadow. Alphas live at the call site
    # because Qt cannot store alpha in a hex token we also reuse opaque.
    "highlight": "#ffffff",
    "shadow": "#000000",
    # The warm shuttle arc rides SEMANTIC.motion; no separate literal.
}

#: Opacity of the modal scrim that dims the workspace behind open dialogs.
#: Heavy enough to read as a separate layer, light enough that the page
#: underneath stays recognizable.
SCRIM_ALPHA = 120
