"""Find clips that describe the same piece of film.

Running Detect Plays twice over one project appends a second full set of
candidates rather than replacing the first, so a project can end up holding
several identical copies of every play. Before overlapping clips were drawn
in separate lanes this was invisible: the copies painted exactly on top of
each other and only showed up as duplicated exports.

Nothing here deletes anything. It reports groups and proposes which member to
keep, and the caller decides.
"""

from __future__ import annotations

from dataclasses import dataclass

from tapesift.models.clip import Clip

# Two detector runs over one source produce byte-identical boundaries, so the
# default is exact. The tolerance exists for clips that were nudged by hand
# after being duplicated.
DEFAULT_TOLERANCE_MS = 0


@dataclass(frozen=True)
class DuplicateGroup:
    """One piece of film claimed by more than one clip."""

    keep: Clip
    remove: tuple[Clip, ...]

    @property
    def clips(self) -> tuple[Clip, ...]:
        return (self.keep, *self.remove)

    @property
    def start_ms(self) -> int:
        return self.keep.start_ms

    @property
    def end_ms(self) -> int:
        return self.keep.end_ms


def metadata_score(clip: Clip) -> int:
    """How much human work a clip carries.

    Used only to choose which copy to keep. A logged clip always outranks an
    untouched one, because losing typed metadata is the expensive mistake.
    """
    score = 0
    score += 3 * sum(1 for value in clip.details.values() if str(value).strip())
    score += 2 * sum(1 for tag in clip.tags if str(tag).strip())
    if clip.notes.strip():
        score += 3
    if clip.clip_title.strip():
        score += 1
    if clip.label.strip():
        score += 1
    return score


def _preferred(clips: list[Clip]) -> Clip:
    """Keep the most-logged copy, and the earliest of equals."""
    return max(
        clips,
        key=lambda clip: (
            metadata_score(clip),
            -clip.order_index,
            -clip.clip_number,
        ),
    )


def find_duplicate_groups(
        clips: list[Clip],
        tolerance_ms: int = DEFAULT_TOLERANCE_MS,
        ) -> list[DuplicateGroup]:
    """Group clips whose ranges match within `tolerance_ms` on both edges.

    Grouping is transitive only through the first member of a group, so a
    chain of slightly-shifted clips cannot collapse into one enormous group.
    """
    tolerance = max(0, int(tolerance_ms))
    ordered = sorted(clips, key=lambda clip: (clip.start_ms, clip.end_ms))
    groups: list[list[Clip]] = []
    for clip in ordered:
        for group in groups:
            anchor = group[0]
            if abs(clip.start_ms - anchor.start_ms) <= tolerance and \
                    abs(clip.end_ms - anchor.end_ms) <= tolerance:
                group.append(clip)
                break
        else:
            groups.append([clip])

    result: list[DuplicateGroup] = []
    for group in groups:
        if len(group) < 2:
            continue
        keep = _preferred(group)
        remove = tuple(clip for clip in group if clip.id != keep.id)
        result.append(DuplicateGroup(keep=keep, remove=remove))
    return result


def removable_clip_ids(groups: list[DuplicateGroup]) -> list[str]:
    return [clip.id for group in groups for clip in group.remove]
