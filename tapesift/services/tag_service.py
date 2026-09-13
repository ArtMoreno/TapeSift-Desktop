"""Tag canonicalisation and human-readable display titles.

Tags are typed by hand across many sessions, so the same tag arrives in
several forms - "Mark Fletcher Jr.", "Mark Fletcher jr", "mark fletcher jr".
Searching splits those into separate results, which makes the library feel
unreliable.

Everything here is display/index-side only: the tags stored on a clip are
never rewritten, so no metadata the user typed is lost.
"""

from __future__ import annotations

import re
from collections import Counter

_SPACES = re.compile(r"\s+")
_TRAILING_PUNCT = re.compile(r"[.,;:]+$")
# "MarkFletcherJRBigRun" -> split before capitals that start a new word,
# keeping runs of capitals (JR, TD, QB) together.
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_SEPARATORS = re.compile(r"[_\-]+")


def tag_key(tag: str) -> str:
    """Matching key for a tag: case- and spacing-insensitive.

    "Mark Fletcher Jr.", "mark fletcher jr" and "Mark  Fletcher  Jr"
    all collapse to the same key.
    """
    cleaned = _SPACES.sub(" ", (tag or "").strip()).lower()
    return _TRAILING_PUNCT.sub("", cleaned)


def tidy_tag(tag: str) -> str:
    """Trim and collapse whitespace without changing the user's casing."""
    return _SPACES.sub(" ", (tag or "").strip())


def _casing_score(value: str) -> tuple[int, int]:
    """Rank spellings by how much they look like a proper label.

    Title-cased words score well ("Run", "CJ Daniels"); SHOUTED words are
    penalised ("RUN"), except short ones that are genuinely acronyms
    ("CJ", "TD", "QB").
    """
    words = value.split()
    shouted = sum(1 for w in words if w.isupper() and len(w) > 2)
    titleish = sum(1 for w in words if w[:1].isupper() and not w.isupper())
    acronyms = sum(1 for w in words if w.isupper() and len(w) <= 2)
    return (titleish + acronyms - shouted, -len(value))


def canonical_display(variants: list[str]) -> str:
    """Pick one display form for a group of equivalent tags.

    The spelling the user typed most often wins; ties break toward the one
    that reads best ("CJ Daniels" over "cj daniels", "Run" over "RUN").
    """
    tidied = [tidy_tag(v) for v in variants if tidy_tag(v)]
    if not tidied:
        return ""
    counts = Counter(tidied)
    best_count = max(counts.values())
    top = [v for v, c in counts.items() if c == best_count]
    return max(top, key=_casing_score)


def merge_tag_variants(tags: list[str]) -> dict[str, str]:
    """Map each tag key to its canonical display form."""
    groups: dict[str, list[str]] = {}
    for tag in tags:
        key = tag_key(tag)
        if key:
            groups.setdefault(key, []).append(tag)
    return {key: canonical_display(values) for key, values in groups.items()}


def canonical_player_display(variants: list[str]) -> str:
    """Choose the most readable spelling for one player's name.

    Player entry is a name field, so a proper/acronym casing wins over a
    repeated lowercase spelling (``Jordan Davis`` stays that way even when
    older clips contain many ``jordan davis`` values).
    """
    tidied = [tidy_tag(value) for value in variants if tidy_tag(value)]
    if not tidied:
        return ""
    counts = Counter(tidied)
    return max(
        counts,
        key=lambda value: (
            _casing_score(value)[0],
            counts[value],
            _casing_score(value)[1],
        ),
    )


def merge_player_variants(tags: list[str]) -> dict[str, str]:
    """Map equivalent player-name spellings to one readable display value."""
    groups: dict[str, list[str]] = {}
    for tag in tags:
        key = tag_key(tag)
        if key:
            groups.setdefault(key, []).append(tag)
    return {
        key: canonical_player_display(values)
        for key, values in groups.items()
    }


def dedupe_tags(tags: list[str]) -> list[str]:
    """Drop equivalent duplicates from one clip's tag list, keeping order."""
    seen: set[str] = set()
    out: list[str] = []
    for tag in tags:
        key = tag_key(tag)
        if key and key not in seen:
            seen.add(key)
            out.append(tidy_tag(tag))
    return out


def clean_display_title(raw: str) -> str:
    """Make a machine-ish name readable.

    "MarkFletcherJRBigRun" -> "Mark Fletcher JR Big Run"
    "001_Opening-sequence" -> "001 Opening sequence"
    The underlying clip title and filename are never modified.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    text = _SEPARATORS.sub(" ", text)
    text = _CAMEL_BOUNDARY.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def display_title(clip_title: str, label: str = "", primary_tag: str = "",
                  filename_base: str = "") -> str:
    """Best available human title, in the documented fallback order."""
    if clip_title and clip_title.strip():
        return clip_title.strip()
    if label and primary_tag:
        return f"{label.strip()} · {primary_tag.strip()}"
    if label.strip():
        return label.strip()
    if primary_tag.strip():
        return primary_tag.strip()
    cleaned = clean_display_title(filename_base)
    return cleaned or "Untitled Clip"
