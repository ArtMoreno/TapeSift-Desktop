"""Structured clip detail fields (film-breakdown metadata) and auto-naming.

Details are free-form key/value strings stored per clip. The built-in field
set targets game-film review but the storage is generic, so other schemas
can be added later without touching the database.
"""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path

from tapesift.models.clip import Clip
from tapesift.services.result_service import legacy_result_key, split_results

# (key, display label) - display order matters: it drives auto-naming and tags.
DETAIL_FIELDS: list[tuple[str, str]] = [
    ("quarter", "Quarter"),
    ("down_distance", "Down & Distance"),
    ("ball_on", "Ball On"),
    ("yards", "Yards"),
    ("yac", "Yards After Catch"),
    ("run_pass", "Run / Pass"),
    ("play_type", "Play Type"),
    # Independent modifier: play action can accompany either a run or pass
    # label, so it must not compete for the single Play Type value.
    ("play_action", "Play Action"),
    ("off_formation", "Off. Formation"),
    ("off_personnel", "Off. Personnel"),
    ("def_formation", "Def. Formation"),
    ("def_personnel", "Def. Personnel"),
    # Why you clipped it - the actor's action, never the play's outcome.
    ("action", "Action"),
    ("result", "Result of Play"),
    ("player_name", "Player Name"),
    ("other_players", "Other Players"),
    ("quarterback", "Quarterback"),
]

#: Players named here are searchable too, but only player_name leads the
#: clip name. Comma-separated.
OTHER_PLAYERS_KEY = "other_players"

DETAIL_KEYS = [key for key, _ in DETAIL_FIELDS]


def merge_editor_details(current: dict[str, str], edited: dict[str, str]) -> dict[str, str]:
    """Library owns its displayed fields, not unseen live project metadata."""
    result = {k: v for k, v in current.items() if k not in DETAIL_KEYS}
    for key, value in edited.items():
        if value.strip():
            result[key] = value.strip() if key in DETAIL_KEYS else value
        else:
            result.pop(key, None)
    return result


def _clean(details: dict[str, str]) -> dict[str, str]:
    return {k: v.strip() for k, v in details.items() if v and v.strip()}


def split_players(value: str) -> list[str]:
    """Split the Other Players field into individual names."""
    return [p.strip() for p in (value or "").replace(";", ",").split(",")
            if p.strip()]


def quarterback_at(defaults: dict, start_ms: int) -> str:
    """Resolve explicit substitutions in film order, independently of navigation."""
    quarterback = str(defaults.get("quarterback_initial", defaults.get("quarterback", "")))
    changes = defaults.get("quarterback_changes", [])
    if not isinstance(changes, list):
        return quarterback
    valid = [change for change in changes if isinstance(change, dict)
             and isinstance(change.get("start_ms"), int)
             and isinstance(change.get("quarterback"), str)]
    for change in sorted(valid, key=lambda change: change["start_ms"]):
        if change["start_ms"] <= start_ms:
            quarterback = change["quarterback"]
    return quarterback


def with_quarterback_change(defaults: dict, start_ms: int, quarterback: str) -> dict:
    updated = deepcopy(defaults)
    updated.setdefault("quarterback_initial", str(defaults.get("quarterback", "")))
    changes = updated.get("quarterback_changes", [])
    updated["quarterback_changes"] = [change for change in changes
        if isinstance(change, dict) and change.get("start_ms") != start_ms] if isinstance(changes, list) else []
    updated["quarterback_changes"].append({"start_ms": start_ms, "quarterback": quarterback.strip()})
    updated["quarterback"] = quarterback.strip()
    return updated


def compose_clip_name(details: dict[str, str]) -> str:
    """Build a readable clip name from the filled detail fields.

    The player and the action you clipped lead, because that is what the clip
    is *for* - the situation follows:

        "Damon Wilson Pressure - Q2 3rd & 7 on 35 Gun Trips 11 vs Nickel"

    With no player or action it falls back to situation-first naming.
    """
    d = _clean(details)
    lead = " ".join(x for x in (d.get("player_name"), d.get("action")) if x)
    quarterback = d.get("quarterback", "")
    qb_play = (d.get("run_pass", "").casefold() == "pass"
               or d.get("play_type", "").casefold() == "scramble"
               or any(legacy_result_key(result) == "sack" for result in split_results(d.get("result", ""))))
    if quarterback and qb_play:
        if not d.get("player_name"):
            lead = " ".join(x for x in (quarterback, d.get("action")) if x)
        elif d["player_name"].casefold() != quarterback.casefold():
            lead += f" · QB {quarterback}"
    situation: list[str] = []
    quarter = d.get("quarter", "")
    if quarter:
        situation.append(quarter if (quarter.upper().startswith("Q") or quarter.upper().startswith("OT")) else f"Q{quarter}")
    if d.get("down_distance"):
        situation.append(d["down_distance"])
    if d.get("ball_on"):
        situation.append(f"on {d['ball_on']}")
    offense = " ".join(x for x in (d.get("off_formation"), d.get("off_personnel")) if x)
    if offense:
        situation.append(offense)
    defense = " ".join(x for x in (d.get("def_formation"), d.get("def_personnel")) if x)
    if defense:
        situation.append(f"vs {defense}")
    for key in ("run_pass", "play_type", "play_action"):
        if d.get(key):
            situation.append(d[key])
    situation_text = " ".join(situation)

    if lead:
        parts = [lead]
        if situation_text:
            parts.append(situation_text)
        # Only add the result when it says something the action didn't.
        result = d.get("result", "")
        if result and result.lower() != d.get("action", "").lower():
            parts.append(result)
        return " - ".join(parts)

    # No player or action - situation first, as before.
    name = situation_text
    if d.get("result"):
        name = f"{name} - {d['result']}" if name else d["result"]
    return name


def compose_auto_name(details: dict[str, str], tags: list[str]) -> str:
    """Use the existing football name, then add yards and non-mirrored tags."""
    parts = [compose_clip_name(details)]
    if details.get("yards", "").strip():
        parts.append(f"{details['yards'].strip()} yards")
    represented = {value.casefold() for value in details_to_tags(details)}
    for tag in tags:
        tag = tag.strip()
        if tag and tag.casefold() not in represented:
            parts.append(tag)
            represented.add(tag.casefold())
    return " - ".join(part for part in parts if part)


def refresh_generated_title(clip: Clip) -> None:
    """Keep automatic names current across Review, Library and project saves."""
    if clip.uses_auto_name and clip.generated_title == clip.clip_title:
        clip.clip_title = compose_auto_name(clip.details, clip.tags)
        clip.generated_title = clip.clip_title
    else:
        clip.generated_title = None


def details_to_tags(details: dict[str, str]) -> list[str]:
    """Detail values as tags (in field order) for tag folders and filtering.

    Other Players expands into one tag per name so every player involved in
    a play is findable, not just the one the clip is named after.
    """
    d = _clean(details)
    tags: list[str] = []
    for key in DETAIL_KEYS:
        if key not in d:
            continue
        if key in {"yards", "yac"}:
            continue
        if key == OTHER_PLAYERS_KEY:
            tags.extend(split_players(d[key]))
        elif key in {"result", "action"}:
            tags.extend(split_results(d[key]))
        else:
            tags.append(d[key])
    return tags


_VS_SPLIT = re.compile(r"\s+(?:vs\.?|v\.?|@)\s+", re.IGNORECASE)
_SIDE_SUFFIX = re.compile(r"\s+(?:O|D|OFFENSE|DEFENSE|ST)\s*$", re.IGNORECASE)


def opponent_from_filename(filename: str, my_team: str = "") -> str:
    """Guess the opponent from a film's filename.

    "MIAMI O VS. FLORIDA STATE D" -> "Florida State" (when my_team is Miami).
    Returns "" when it can't be confident, rather than guessing wrong.
    """
    stem = Path(filename).stem if filename else ""
    stem = re.sub(r"[_\-]+", " ", stem)
    parts = _VS_SPLIT.split(stem)
    if len(parts) != 2:
        return ""
    sides = [_SIDE_SUFFIX.sub("", p).strip() for p in parts]
    sides = [s for s in sides if s]
    if len(sides) != 2:
        return ""
    if my_team.strip():
        key = my_team.strip().lower()
        others = [s for s in sides if key not in s.lower()]
        # Exactly one side is us - the other is the opponent.
        if len(others) == 1:
            return others[0].title()
        return ""
    return ""


def merge_tags(existing: list[str], new: list[str]) -> list[str]:
    """Append new tags, case-insensitively deduplicated, preserving order."""
    seen = {t.lower() for t in existing}
    merged = list(existing)
    for tag in new:
        if tag.lower() not in seen:
            merged.append(tag)
            seen.add(tag.lower())
    return merged


def sync_detail_tags(existing: list[str], previous: dict[str, str],
                     current: dict[str, str]) -> list[str]:
    """Remove obsolete mirrors, retaining values still referenced by any field."""
    old = {value.casefold() for value in details_to_tags(previous)}
    keep = {value.casefold() for value in details_to_tags(current)}
    old_results = {legacy_result_key(value) for value in split_results(previous.get("result", ""))}
    current_results = {legacy_result_key(value) for value in split_results(current.get("result", ""))}
    # ponytail: legacy tags have no provenance; exact old detail matches are
    # mirrors. Add explicit ownership only if users need same-text free tags.
    return [tag for tag in existing if tag.casefold() in keep or (
        tag.casefold() not in old - keep
        and legacy_result_key(tag) not in old_results - current_results)]
