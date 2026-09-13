"""Jersey numbers, because that is what you can actually see on film.

Nobody watching tape reads a name off a jersey - they read a number, and
then they type a name from memory. That is where "M. Fletcher" and
"Fletcher" and the occasional wrong spelling come from. A roster turns
the thing you can see into the thing the project stores, so the name is
right the first time instead of being reconciled afterwards.

Numbers repeat across a squad: Miami's 4 is both Cam Pruitt at linebacker
and Mark Fletcher Jr. at back. A number that could be two people is
not resolved silently - it is offered, the same way an ambiguous name is
left alone in ``player_name_service``.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from collections.abc import Iterable
from pathlib import Path

ROSTER_DIR = Path(__file__).resolve().parent.parent / "resources" / "rosters"
#: Filename a project keeps its own roster under, beside its clips.
PROJECT_ROSTER_NAME = "roster.csv"
FIELDS = ("number", "name", "position", "class")


@dataclass(frozen=True)
class RosterPlayer:
    number: str
    name: str
    position: str = ""
    year: str = ""

    @property
    def label(self) -> str:
        """How this player reads in a picker: "#4 Mark Fletcher Jr. (RB)"."""
        tail = f" ({self.position})" if self.position else ""
        return f"#{self.number} {self.name}{tail}"


class Roster:
    """A squad, looked up by the number on the shirt."""

    def __init__(self, players: list[RosterPlayer] | None = None) -> None:
        self._players: list[RosterPlayer] = list(players or [])

    def __len__(self) -> int:
        return len(self._players)

    def __iter__(self):
        return iter(self._players)

    @property
    def players(self) -> tuple[RosterPlayer, ...]:
        return tuple(self._players)

    def names(self) -> list[str]:
        """Every name, for autocomplete."""
        return [player.name for player in self._players]

    def by_number(self, number: str) -> list[RosterPlayer]:
        """Everyone wearing that number - often two, on opposite sides."""
        wanted = str(number or "").strip().lstrip("#")
        if not wanted.isdigit():
            return []
        target = str(int(wanted))
        return [
            player for player in self._players
            if player.number.strip().lstrip("#") == target
        ]

    def resolve(self, text: str) -> str:
        """Turn what was typed into a name, when that is unambiguous.

        Returns "" when the text is not a number, or is a number worn by
        more than one player - guessing there puts the wrong man on the
        play, which is worse than leaving the field as typed.
        """
        matches = self.by_number(text)
        return matches[0].name if len(matches) == 1 else ""

    def normalize_entry(self, text: str) -> str:
        """What the player field should store for what was typed.

        "4 Mark Fletcher Jr." is what the completer offers, so the number
        has to come back off before it is saved - otherwise every player
        is two players again, one with a number glued to the front.
        """
        raw = str(text or "").strip()
        if not raw:
            return ""
        head, _, tail = raw.partition(" ")
        if head.lstrip("#").isdigit() and tail.strip():
            return tail.strip()
        resolved = self.resolve(raw)
        return resolved or raw

    def position_of(self, name: str) -> str:
        folded = str(name or "").strip().casefold()
        for player in self._players:
            if player.name.casefold() == folded:
                return player.position
        return ""

    # ------------------------------------------------------------ files

    @classmethod
    def from_rows(cls, rows) -> "Roster":
        players: list[RosterPlayer] = []
        for row in rows:
            name = str(row.get("name", "")).strip()
            if not name:
                continue
            players.append(RosterPlayer(
                number=str(row.get("number", "")).strip().lstrip("#"),
                name=name,
                position=str(row.get("position", "")).strip(),
                year=str(row.get("class", "")).strip(),
            ))
        return cls(players)

    @classmethod
    def load(cls, path: Path) -> "Roster":
        if not path.is_file():
            return cls()
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return cls.from_rows(csv.DictReader(handle))

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            for player in self._players:
                writer.writerow({
                    "number": player.number, "name": player.name,
                    "position": player.position, "class": player.year,
                })
        return path


def strip_jersey_number(text: str, known: Iterable[str] = ()) -> str:
    """"4 Mark Fletcher Jr." -> "Mark Fletcher Jr.", but only from a picker.

    The number is how a player is found on film; it is not part of his
    name, and storing it would make one player two. But it is only safe to
    remove when the picker is what put it there, so this strips a number
    only from strings the roster actually offered.

    "#12 QB" is the counter-example, and a real one: an opponent's names
    are often unknown and a play gets logged by number and position. That
    is the whole label, and taking the number off leaves "QB", which is
    nobody.
    """
    raw = str(text or "").strip()
    offered = {str(item).strip() for item in known}
    if raw not in offered:
        return raw
    head, _, tail = raw.partition(" ")
    if head.lstrip("#").isdigit() and tail.strip():
        return tail.strip()
    return raw


def bundled_rosters() -> dict[str, Path]:
    """Squads that ship with the app, by slug."""
    if not ROSTER_DIR.is_dir():
        return {}
    return {path.stem: path for path in sorted(ROSTER_DIR.glob("*.csv"))}


def load_bundled(slug: str) -> Roster:
    # Settings saved by older releases still point at the retired file.
    slug = "miami_2026" if slug == "miami_2025" else slug
    path = bundled_rosters().get(slug)
    return Roster.load(path) if path else Roster()


def load_for_project(project_folder: Path | None) -> Roster:
    """A project's own roster, if it has one."""
    if project_folder is None:
        return Roster()
    return Roster.load(Path(project_folder) / PROJECT_ROSTER_NAME)
