"""Single source of truth for TapeSift's football vocabulary.

Every football string the app offers — dropdowns, chips, quick tags, result
favourites, and the shipped fixed_details lists — is defined here. Matching
is case- and whitespace-insensitive against canonical spellings only.
Unknown values still store, tag, and filter; they always resolve to the
catch-all category ``Other`` and are never classified as Run/Pass/Score.

Presentation helpers (category, short label, explosive/success derivation)
never write back into clip.details.
"""

from __future__ import annotations

from dataclasses import dataclass

UNKNOWN_CATEGORY = "Other"

KIND_CHOICE = "choice"
KIND_INTEGER = "integer"
KIND_TEXT = "text"

YARDS_KEY = "yards"
ACTION_KEY = "action"

DEFAULT_EXPLOSIVE_RUSH_YARDS = 12
DEFAULT_EXPLOSIVE_PASS_YARDS = 16

# Legend projection for a multi-result play. The full set stays stored;
# only this order decides the single timeline/grid category.
RESULT_CATEGORY_PRIORITY: tuple[str, ...] = (
    "Penalty",
    "Score",
    "Turnover",
    "Negative",
    "First Down",
    "Complete",
    "Stop",
    "Special",
    UNKNOWN_CATEGORY,
)

DEFAULT_RESULT_FAVORITES: tuple[str, ...] = (
    "No Gain",
    "First Down",
    "Reception",
    "Touchdown",
    "Sack",
    "Interception",
)


@dataclass(frozen=True)
class VocabValue:
    """One offered value: the stored spelling, its legend category, a chip face."""

    canonical: str
    category: str
    short: str = ""

    def __post_init__(self) -> None:
        if not self.short:
            object.__setattr__(self, "short", self.canonical)


def _v(canonical: str, category: str, short: str = "") -> VocabValue:
    return VocabValue(canonical, category, short)


_DOWN_DISTANCE = tuple(
    _v(f"{down} & {distance}", "Situation")
    for down in ("1st", "2nd", "3rd", "4th")
    for distance in ["Goal"] + [str(n) for n in range(1, 41)]
)

# field -> (axis, values). Axis is the DETAIL_FIELDS key the values belong to.
_TABLE: dict[str, tuple[VocabValue, ...]] = {
    "run_pass": (
        _v("Run", "Run"),
        _v("Pass", "Pass"),
        _v("Special", "Special"),
        _v("No Play", "No Play"),
    ),
    "play_type": (
        _v("Inside Zone", "Run Concept"),
        _v("Outside Zone", "Run Concept"),
        _v("Split Zone", "Run Concept"),
        _v("Duo", "Run Concept"),
        _v("Power", "Run Concept"),
        _v("Counter", "Run Concept"),
        _v("Trap", "Run Concept"),
        _v("Iso", "Run Concept"),
        _v("Draw", "Run Concept"),
        _v("Sweep", "Run Concept"),
        _v("Toss", "Run Concept"),
        _v("Dive", "Run Concept"),
        _v("Pitch", "Run Concept"),
        _v("Option", "Run Concept"),
        _v("Reverse", "Run Concept"),
        _v("Jet Sweep", "Run Concept"),
        _v("End Around", "Run Concept"),
        _v("Read Option", "Run Concept"),
        _v("Triple Option", "Run Concept"),
        _v("QB Sneak", "QB"),
        _v("Designed QB Run", "QB"),
        _v("Scramble", "QB"),
        _v("Dropback", "Pass Concept"),
        _v("Screen", "Pass Concept"),
        _v("RPO", "RPO"),
        _v("Boot", "Pass Concept"),
        _v("Rollout", "Pass Concept"),
        _v("Sprint Out", "Pass Concept"),
        _v("Quick Game", "Pass Concept"),
        _v("Mesh", "Pass Concept"),
        _v("Flood", "Pass Concept"),
        _v("Four Verticals", "Pass Concept", "Verts"),
        _v("Smash", "Pass Concept"),
        _v("Stick", "Pass Concept"),
        _v("Dagger", "Pass Concept"),
        _v("Shallow Cross", "Pass Concept"),
        _v("Trick", "Trick"),
    ),
    "play_action": (
        _v("Play Action", "Play Action"),
    ),
    "action": (
        _v("Explosive Play", "Highlight"),
        _v("Big Gain", "Highlight"),
        _v("Catch", "Receiving"),
        _v("Run", "Ball Carrying"),
        _v("One-Handed Catch", "Receiving"),
        _v("Contested Catch", "Receiving"),
        _v("Diving Catch", "Receiving"),
        _v("Sideline Catch", "Receiving"),
        _v("Stiff Arm", "Ball Carrying"),
        _v("Juke", "Ball Carrying"),
        _v("Spin Move", "Ball Carrying"),
        _v("Hurdle", "Ball Carrying"),
        _v("Pressure", "Pass Rush"),
        _v("Missed Tackle", "Contact"),
        _v("Tackle", "Contact"),
        _v("Big Hit", "Contact"),
        _v("Coverage", "Coverage"),
        _v("Block", "Block"),
        _v("PBU", "Coverage"),
        _v("Forced Fumble", "Disruption"),
        _v("QB Hit", "Pass Rush"),
        _v("Hurry", "Pass Rush"),
        _v("Contain", "Pass Rush"),
        _v("Blitz", "Pass Rush"),
        _v("Tackle Assist", "Contact"),
        _v("Broken Tackle", "Contact"),
        _v("Missed Block", "Block"),
        _v("Run Block", "Block"),
        _v("Pass Block", "Block"),
        _v("Lead Block", "Block"),
        _v("Pull", "Block"),
        _v("Pancake", "Block"),
        _v("Route", "Route"),
    ),
    "result": (
        # Passing
        _v("Completion", "Complete", "Comp"),
        _v("Incompletion", "Stop", "Inc"),
        _v("Interception", "Turnover", "INT"),
        _v("Sack", "Negative"),
        _v("Drop", "Stop"),
        _v("Throwaway", "Stop"),
        _v("Batted Pass", "Stop"),
        _v("Spike", "Stop"),
        _v("Reception", "Complete", "Rec"),
        _v("YAC", "Complete"),
        # Running
        _v("Gain", "Complete"),
        _v("No Gain", "Stop"),
        _v("Loss", "Negative"),
        _v("TFL", "Negative"),
        _v("Fumble", "Negative"),
        _v("Fumble Lost", "Turnover", "Fum Lost"),
        _v("Fumble Recovered", "Complete"),
        # Down
        _v("First Down", "First Down", "1st Dn"),
        _v("3rd Down Conversion", "First Down", "3rd Conv"),
        _v("4th Down Conversion", "First Down", "4th Conv"),
        _v("Turnover on Downs", "Turnover", "TOD"),
        # Scoring
        _v("Touchdown", "Score", "TD"),
        _v("Field Goal Good", "Score", "FG Good"),
        _v("Field Goal Missed", "Stop", "FG Miss"),
        _v("Field Goal Blocked", "Special", "FG Blk"),
        _v("Safety", "Score"),
        _v("Extra Point Good", "Score", "XP Good"),
        _v("Extra Point Missed", "Stop", "XP Miss"),
        _v("Extra Point Blocked", "Special", "XP Blk"),
        _v("2-Point Good", "Score", "2pt"),
        _v("2-Point Failed", "Stop", "2pt Fail"),
        # Kicking
        _v("Punt Returned", "Special"),
        _v("Punt Fair Catch", "Special"),
        _v("Punt Downed", "Special"),
        _v("Punt Out of Bounds", "Special", "OOB"),
        _v("Punt Blocked", "Special"),
        _v("Punt Muffed", "Special"),
        _v("Kickoff Returned", "Special"),
        _v("Kickoff Touchback", "Special"),
        _v("Onside Recovered", "Special"),
        _v("Onside Failed", "Special"),
        _v("Touchback", "Special"),
        # Admin
        _v("Penalty Accepted", "Penalty"),
        _v("Penalty Declined", "Penalty"),
        _v("Offsetting Penalties", "Penalty"),
        # A foul name and its ruling can coexist. Neither erases the play
        # nor changes its family; No Play must be explicitly recorded.
        _v("Offensive Holding", "Penalty", "Off Hold"),
        _v("Defensive Holding", "Penalty", "Def Hold"),
        _v("Offensive Pass Interference", "Penalty", "OPI"),
        _v("Defensive Pass Interference", "Penalty", "DPI"),
        _v("False Start", "Penalty"),
        _v("Offside", "Penalty"),
        _v("Neutral Zone Infraction", "Penalty", "NZ Inf"),
        _v("Encroachment", "Penalty"),
        _v("Delay of Game", "Penalty", "Delay"),
        _v("Intentional Grounding", "Penalty", "Grounding"),
        _v("Illegal Formation", "Penalty"),
        _v("Illegal Motion", "Penalty"),
        _v("Illegal Shift", "Penalty"),
        _v("Illegal Substitution", "Penalty"),
        _v("Ineligible Downfield", "Penalty"),
        _v("Illegal Forward Pass", "Penalty"),
        _v("Illegal Contact", "Penalty"),
        _v("Roughing the Passer", "Penalty", "Rough Passer"),
        _v("Face Mask", "Penalty"),
        _v("Targeting", "Penalty"),
        _v("Personal Foul", "Penalty"),
        _v("Unsportsmanlike Conduct", "Penalty", "Unsportsmanlike"),
        _v("Illegal Block in the Back", "Penalty", "Block in Back"),
        _v("Chop Block", "Penalty"),
        _v("Clipping", "Penalty"),
        _v("No Play", "Special"),
        _v("Kneel", "Special"),
    ),
    "quarter": (
        _v("Q1", "Situation"),
        _v("Q2", "Situation"),
        _v("Q3", "Situation"),
        _v("Q4", "Situation"),
        _v("OT", "Situation"),
    ),
    "down_distance": _DOWN_DISTANCE,
}

VOCAB_FIELDS: tuple[str, ...] = tuple(_TABLE.keys())

_INTEGER_FIELDS = frozenset({YARDS_KEY})

# Spellings the old shipped lists offered that now live on a different axis
# or were spelling forks. Stripped on settings load; never re-offered.
_REMOVED: dict[str, frozenset[str]] = {
    "play_type": frozenset({
        "run", "pass", "play action", "punt", "field goal", "kickoff",
    }),
    "result": frozenset({
        "explosive", "td", "int", "incomplete", "penalty", "field goal",
        "punt", "kickoff",
    }),
    "action": frozenset({
        "sack", "interception", "reception", "touchdown", "explosive",
    }),
}


def _fold(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _index_for(field: str) -> dict[str, VocabValue]:
    return {_fold(item.canonical): item for item in _TABLE.get(field, ())}


_INDEX: dict[str, dict[str, VocabValue]] = {
    field: _index_for(field) for field in _TABLE
}


def field_kind(field: str) -> str:
    """How a detail field is edited: choice list, integer, or free text."""
    if field in _INTEGER_FIELDS:
        return KIND_INTEGER
    if field in _TABLE:
        return KIND_CHOICE
    return KIND_TEXT


def values_for(field: str) -> list[str]:
    """Canonical spellings offered for ``field``, in table order."""
    return [item.canonical for item in _TABLE.get(field, ())]


def categories_for(field: str) -> list[str]:
    """Unique legend categories for ``field``, in first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for item in _TABLE.get(field, ()):
        if item.category not in seen:
            seen.add(item.category)
            out.append(item.category)
    return out


def lookup(field: str, value: object) -> VocabValue | None:
    """Return the table entry for a known spelling, or None if unknown."""
    folded = _fold(value)
    if not folded:
        return None
    return _INDEX.get(field, {}).get(folded)


def category_for(field: str, value: object) -> str:
    """Legend category for a value. Unknown (or blank) is always Other."""
    if not _fold(value):
        return UNKNOWN_CATEGORY
    item = lookup(field, value)
    return item.category if item is not None else UNKNOWN_CATEGORY


def short_for(field: str, value: object) -> str:
    """Chip-sized label; unknown values display as typed."""
    text = " ".join(str(value or "").split())
    item = lookup(field, value)
    return item.short if item is not None else text


def default_fixed_details() -> dict[str, list[str]]:
    """Shipped dropdown lists, keyed like AppSettings.fixed_details."""
    return {field: values_for(field) for field in VOCAB_FIELDS}


def heal_fixed_details(
        stored: dict[str, list[str]] | None,
        hidden: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
    """Rebuild dropdown lists from the table, keeping user extras.

    Removed overlapping spellings (Run-as-play-type, TD-as-result, Sack-as-
    action) are dropped. A leftover ``highlight`` list is folded into
    ``action``. Explicitly hidden choices stay out of dropdowns, but remain
    recognized vocabulary so hiding a choice never reclassifies saved clips.
    """
    stored = stored or {}
    healed = default_fixed_details()
    incoming_action = [
        *(stored.get(ACTION_KEY) or []),
        *(stored.get("highlight") or []),
    ]
    for field, defaults in healed.items():
        incoming = incoming_action if field == ACTION_KEY else list(
            stored.get(field) or [])
        removed = _REMOVED.get(field, frozenset())
        seen = {_fold(value) for value in defaults}
        extras: list[str] = []
        for raw in incoming:
            text = " ".join(str(raw).split())
            key = _fold(text)
            if not key or key in seen or key in removed:
                continue
            extras.append(text)
            seen.add(key)
        if extras:
            healed[field] = [*defaults, *extras]
    for key, values in stored.items():
        if key in healed or key == "highlight" or not isinstance(values, list):
            continue
        cleaned = [" ".join(str(item).split()) for item in values
                   if " ".join(str(item).split())]
        if cleaned:
            healed[key] = cleaned
    for field, values in (hidden or {}).items():
        if field not in _TABLE or not isinstance(values, list):
            continue
        excluded = {_fold(value) for value in values if lookup(field, value)}
        healed[field] = [value for value in healed[field]
                         if _fold(value) not in excluded]
    return healed


def parse_yards(details: dict[str, str] | object) -> int | None:
    """Integer yards from a details dict or a raw string. None if unset/invalid."""
    if isinstance(details, dict):
        raw = details.get(YARDS_KEY, "")
    else:
        raw = details
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def is_explosive(
        details: dict[str, str],
        rush_threshold: int = DEFAULT_EXPLOSIVE_RUSH_YARDS,
        pass_threshold: int = DEFAULT_EXPLOSIVE_PASS_YARDS,
) -> bool:
    """True when yards meet the family threshold. Never writes clip.details."""
    yards = parse_yards(details)
    if yards is None:
        return False
    family = _fold(details.get("run_pass", ""))
    if family == "run":
        return yards >= rush_threshold
    if family == "pass":
        return yards >= pass_threshold
    return False


def is_successful(details: dict[str, str]) -> bool | None:
    """Compatibility entrypoint; situation derivation belongs to context."""
    from tapesift.services.football_context import is_successful as derive_success
    return derive_success(details)
