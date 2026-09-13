"""One player, however their name got typed.

Primary Player is free text, logged at speed, often from a roster sheet
and often from memory. The same player arrives as "Mark Fletcher",
"M. Fletcher", "Fletcher", "Fletcher, Mark" and "Mark Fletcher Jr." over
one game. Every one of those is a separate row in the grid, a separate
colour, and a separate line in the heat map - which quietly turns a
workload picture into a lie.

Case, spacing and punctuation already collapsed through
``canonical_tag_key``. This handles the rest, and only the rest: a name
is folded into another when it cannot be anyone else on this roster.
Ambiguity is left alone, because merging two real players is a worse
error than showing one player twice.
"""

from __future__ import annotations

from collections.abc import Iterable

from tapesift.services.tag_style_service import canonical_tag_key

#: Generational suffixes are part of a name but never distinguish two
#: players on the same roster - nobody fields Mark Fletcher and Mark
#: Fletcher Jr. in the same game.
SUFFIXES = frozenset({"jr", "jnr", "sr", "snr", "ii", "iii", "iv", "v"})


def _tokens(name: str) -> list[str]:
    """Words of a name, lower case, without punctuation or suffixes."""
    cleaned = canonical_tag_key(name).split("_")
    return [word for word in cleaned if word and word not in SUFFIXES]


def name_parts(name: str) -> tuple[str, str]:
    """(first, last), reading "Fletcher, Mark" the same as "Mark Fletcher".

    A comma is the only reliable signal of reversed order. Without one,
    the last word is the surname, which is how these are typed.
    """
    if "," in name:
        tail, _, head = name.partition(",")
        words = _tokens(f"{head} {tail}")
    else:
        words = _tokens(name)
    if not words:
        return "", ""
    if len(words) == 1:
        return "", words[0]
    return words[0], words[-1]


def _compatible(first_a: str, first_b: str) -> bool:
    """Could these be the same person's first name?

    An initial matches the full name it opens. Two different full names
    never match, and two bare initials that differ never match.
    """
    if not first_a or not first_b:
        return True
    if first_a == first_b:
        return True
    if len(first_a) == 1:
        return first_b.startswith(first_a)
    if len(first_b) == 1:
        return first_a.startswith(first_b)
    return False


def _completeness(name: str) -> tuple[int, int, int, int]:
    """How full a spelling is - the fullest becomes the display name.

    Natural order beats "Fletcher, Mark" even though both carry the same
    two words: a roster reads one way and a sort key reads the other, and
    the name shown on screen should be the one a person would say.
    """
    first, last = name_parts(name)
    natural = 0 if "," in name else 1
    return (1 if first else 0, natural, len(first), len(name.strip()))


def resolve_roster(names: Iterable[str]) -> dict[str, str]:
    """Map every spelling seen to the one name that should be shown.

    Returns {canonical_tag_key(raw): display name}. A spelling folds into
    a fuller one only when exactly one player it could belong to exists;
    two candidates means the shorter form stays on its own rather than
    being guessed at.
    """
    seen: dict[str, str] = {}
    for raw in names:
        text = str(raw or "").strip()
        if not text:
            continue
        key = canonical_tag_key(text)
        if key and (key not in seen
                    or _completeness(text) > _completeness(seen[key])):
            seen[key] = text

    # Group by surname first: nothing folds across different surnames.
    by_last: dict[str, list[str]] = {}
    for key, text in seen.items():
        by_last.setdefault(name_parts(text)[1], []).append(key)

    resolved: dict[str, str] = {}
    for last, keys in by_last.items():
        # Fullest spellings first, so shorter ones fold into them.
        ordered = sorted(keys, key=lambda k: _completeness(seen[k]),
                         reverse=True)
        anchors: list[str] = []
        for key in ordered:
            first = name_parts(seen[key])[0]
            matches = [
                anchor for anchor in anchors
                if _compatible(first, name_parts(seen[anchor])[0])
            ]
            # Exactly one candidate is a merge. None is a new player. More
            # than one is ambiguous, and guessing there is the one error
            # worth avoiding.
            if len(matches) == 1:
                resolved[key] = seen[matches[0]]
            else:
                anchors.append(key)
                resolved[key] = seen[key]
    return resolved


def resolved_key(name: str, roster: dict[str, str]) -> str:
    """The key a name should count under, given a resolved roster."""
    key = canonical_tag_key(str(name or "").strip())
    if not key:
        return ""
    display = roster.get(key)
    return canonical_tag_key(display) if display else key
