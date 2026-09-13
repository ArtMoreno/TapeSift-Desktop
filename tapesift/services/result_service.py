"""Compatibility-safe helpers for a play's ordered result set.

Clip details are intentionally stored as strings in projects and the
database.  Multiple results therefore use one reserved separator at the
storage boundary while the rest of the application works with list-like
helpers.  A legacy one-result value is already a valid one-item set.
"""

from __future__ import annotations

from collections.abc import Iterable


RESULT_SEPARATOR = "; "
_CATCH_PAIR = ("Completion", "Reception")
_CATCH_KEYS = {"completion", "reception"}

# Frozen pre-vocabulary equality rules for existing projects. Chips still write
# canonical names; category_for never uses this compatibility table.
_LEGACY_ALIASES = {
    "1st down": "first down", "td": "touchdown",
    "int": "interception", "incomplete": "incompletion",
}


def result_key(value: str) -> str:
    """Return a comparison key without changing the displayed spelling."""
    return " ".join(str(value or "").strip().casefold().split())


def legacy_result_key(value: str) -> str:
    """Exact legacy selection/style equality, never vocabulary classification."""
    folded = result_key(value)
    return _LEGACY_ALIASES.get(folded, folded)


def split_results(value: str) -> list[str]:
    """Read a legacy single result or a semicolon-delimited result set."""
    values: list[str] = []
    seen: set[str] = set()
    for raw in str(value or "").split(";"):
        item = " ".join(raw.strip().split())
        key = result_key(item)
        if not item or not key or key in seen:
            continue
        values.append(item)
        seen.add(key)
    return values


def join_results(values: Iterable[str]) -> str:
    """Serialize results in stable order, flattening any combined inputs."""
    joined: list[str] = []
    seen: set[str] = set()
    for value in values:
        for item in split_results(value):
            key = result_key(item)
            if key in seen:
                continue
            joined.append(item)
            seen.add(key)
    return RESULT_SEPARATOR.join(joined)


def has_result(stored: str, candidate: str) -> bool:
    """Whether any stored result is equivalent to ``candidate``."""
    candidates = {legacy_result_key(item) for item in split_results(candidate)}
    if not candidates:
        return False
    return any(
        legacy_result_key(item) in candidates for item in split_results(stored))


def add_results(stored: str, *values: str) -> str:
    """Add results, pairing catches and replacing a contradictory catch outcome."""
    selected = split_results(stored)
    requested = [item for value in values for item in split_results(value)]
    yardage_keys = {"gain", "no gain", "loss"}
    yardage = [result_key(item) for item in requested if result_key(item) in yardage_keys]
    if yardage:
        excluded = yardage_keys - {yardage[-1]}
        selected = [item for item in selected if result_key(item) not in excluded]
        requested = [item for item in requested if result_key(item) not in excluded]
    # The latest explicit catch/incomplete choice replaces its opposite.
    # Loading old results still preserves exactly what was recorded.
    outcomes = [legacy_result_key(item) for item in requested
                if legacy_result_key(item) in _CATCH_KEYS | {"incompletion", "drop"}]
    if outcomes:
        excluded = {"incompletion", "drop"} if outcomes[-1] in _CATCH_KEYS else _CATCH_KEYS
        selected = [item for item in selected if legacy_result_key(item) not in excluded]
        requested = [item for item in requested if legacy_result_key(item) not in excluded]
    seen = {legacy_result_key(item) for item in selected}
    # Link deliberate selections only. Loading/serializing old film never
    # invents tags or changes its existing statistical interpretation.
    if any(legacy_result_key(item) in _CATCH_KEYS for item in requested):
        requested.extend(_CATCH_PAIR)
    implied = {"drop": "Incompletion", "fumble lost": "Fumble"}
    requested.extend(implied[key] for item in tuple(requested)
                     if (key := legacy_result_key(item)) in implied)
    for item in requested:
        key = legacy_result_key(item)
        if key not in seen:
            selected.append(item)
            seen.add(key)
    return join_results(selected)


def remove_result(stored: str, candidate: str) -> str:
    """Remove a selected result and its linked catch partner, preserving others."""
    remove_keys = {legacy_result_key(item) for item in split_results(candidate)}
    if remove_keys & _CATCH_KEYS:
        remove_keys |= _CATCH_KEYS
    return join_results(
        item for item in split_results(stored)
        if legacy_result_key(item) not in remove_keys
    )


def toggle_result(stored: str, candidate: str) -> str:
    """Toggle a chip/menu value in an ordered result set."""
    if has_result(stored, candidate):
        return remove_result(stored, candidate)
    return add_results(stored, candidate)


def result_conflicts(stored: str) -> list[str]:
    """Advisory only: preserve the recorded results for the user to resolve."""
    keys = {legacy_result_key(item) for item in split_results(stored)}
    conflicts = []
    if keys & _CATCH_KEYS and "incompletion" in keys:
        conflicts.append("Catch and incompletion are both selected.")
    if {"gain", "loss"} <= keys:
        conflicts.append("Gain and loss are both selected.")
    return conflicts


def primary_result(stored: str) -> str:
    """Choose one stable legend category from a multi-result play.

    The full set remains stored and visible in Play Details.  This projection
    only prevents combinations such as "Reception; First Down" from creating
    an ever-growing number of timeline legend categories.
    """
    from tapesift.services.football_vocab import (
        RESULT_CATEGORY_PRIORITY, category_for,
    )

    # Classification sees the recorded spellings, independently of legacy
    # selection equality (TD alone is Other; Touchdown is Score).
    values = split_results(stored)
    if not values:
        return ""
    present = {category_for("result", value) for value in values}
    for category in RESULT_CATEGORY_PRIORITY:
        if category in present:
            return category
    return category_for("result", values[0])
