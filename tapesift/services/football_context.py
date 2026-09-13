"""Saved football context and team identities, independent of playback."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
import math
from pathlib import Path
import re

TEAM_ASSETS = Path(__file__).resolve().parents[1] / "resources" / "teams"


@lru_cache(maxsize=1)
def teams() -> dict[str, dict[str, str]]:
    rows = json.loads((TEAM_ASSETS / "catalog.json").read_text(encoding="utf-8"))
    return {row["id"]: row for row in rows}


def team_name(team_id: str) -> str:
    return teams().get(team_id, {}).get("name", team_id or "Choose team")


def team_abbreviation(team_id: str) -> str:
    team = teams().get(team_id, {})
    return team.get("abbreviation") or team.get("name") or "Unassigned"


def logo_path(team_id: str) -> Path | None:
    match = re.fullmatch(r"cfbd:team:(\d+)", team_id)
    path = TEAM_ASSETS / (match[1] + ".png") if match else None
    return path if path and path.is_file() else None


def parse_ball(value: str) -> tuple[str, int | None]:
    value = value.strip().upper()
    if value in {"MID", "MIDFIELD", "50", "OWN 50", "OPP 50"}:
        return "MID", 50
    match = re.fullmatch(r"(?:(OWN|OPP)\s*)?(\d{1,2})", value)
    if match and 0 <= int(match[2]) <= 50:
        return match[1] or "", int(match[2])
    return "", None


def parse_down_distance(value: str) -> tuple[str, str]:
    value = re.sub(r"\bg\b", "Goal", value.strip(), flags=re.IGNORECASE)
    match = re.fullmatch(
        r"\s*([1-4])(?:st|nd|rd|th)?(?:\s*(?:&|and)\s*|\s+)(\d+|goal|inches)\s*",
        value, re.IGNORECASE)
    if match:
        return match[1], match[2].title() if not match[2].isdigit() else match[2]
    only_down = re.fullmatch(r"\s*([1-4])(?:st|nd|rd|th)?\s*", value, re.IGNORECASE)
    if only_down:
        return only_down[1], ""
    only_distance = re.fullmatch(r"\s*to\s+go\s+(\d+|goal|inches)\s*", value, re.IGNORECASE)
    if only_distance:
        return "", only_distance[1].title() if not only_distance[1].isdigit() else only_distance[1]
    return "", ""


def down_distance(down: str, distance: str) -> str:
    if not down and not distance:
        return ""
    ordinal = {"1": "1st", "2": "2nd", "3": "3rd", "4": "4th"}.get(down, "")
    return f"{ordinal} & {distance}" if ordinal and distance else ordinal or (f"To Go {distance}" if distance else "")


def spot_label(yards: float) -> str:
    if yards == 50:
        return "MIDFIELD"
    if yards < 50:
        return f"OWN {yards:g}"
    return f"OPP {100-yards:g}"


@dataclass(frozen=True)
class FieldContext:
    los_yards: float | None = None
    to_gain_yards: float | None = None
    offense_id: str = ""
    direction: str = ""
    distance: float | None = None
    goal_to_go: bool = False
    message: str = "Set ball position and own / opponent territory."

    @classmethod
    def from_details(cls, details: dict[str, str], game_team_ids: list[str]) -> FieldContext:
        offense = details.get("offense_team_id", "")
        # This is an offense-relative diagram, not the camera's orientation.
        # Neither team identity nor filmed direction is needed to plot a spot.
        base = dict(offense_id=offense if offense in game_team_ids else "",
                    direction="right")
        side, number = parse_ball(details.get("ball_on", ""))
        if number is None or not side:
            return cls(**base, message="Set ball position and OWN / OPP side.")
        los = float(100-number if side == "OPP" else number)
        down, distance_text = parse_down_distance(details.get("down_distance", ""))
        if not distance_text:
            return cls(**base, los_yards=los, message="Set down and distance for the line to gain.")
        goal = distance_text == "Goal"
        if distance_text == "Inches":
            return cls(**base, los_yards=los, message="Inches to gain; exact distance unknown.")
        distance = 100-los if goal else float(distance_text)
        target = los+distance
        if target > 100:
            return cls(**base, los_yards=los,
                       message="Distance extends past the goal line. Check the situation.")
        return cls(**base, los_yards=los, to_gain_yards=target,
                   distance=distance, goal_to_go=goal, message="")

    def visible_range(self) -> tuple[float, float]:
        if self.los_yards is None:
            return 0, 100
        target = self.to_gain_yards if self.to_gain_yards is not None else self.los_yards
        start = max(0, math.floor((min(self.los_yards, target)-4)/5)*5)
        end = min(100, math.ceil((max(self.los_yards, target)+5)/5)*5)
        if end-start < 20:
            start = max(0, min(start, end-20))
            end = min(100, start+20)
        return start, end

    def fraction(self, yards: float) -> float:
        start, end = self.visible_range()
        value = (yards-start)/(end-start)
        return 1-value if self.direction == "left" else value


def is_successful(details: dict[str, str]) -> bool | None:
    """Derive success from recorded situation; missing measurements stay unknown."""
    from tapesift.services.football_vocab import category_for, parse_yards
    from tapesift.services.result_service import split_results

    if category_for("run_pass", details.get("run_pass", "")) == "No Play":
        return None
    down, distance = parse_down_distance(details.get("down_distance", ""))
    if not down:
        return None
    yards = parse_yards(details)
    if distance == "Goal":
        if any(category_for("result", value) == "Score"
               for value in split_results(details.get("result", ""))):
            return True
        side, number = parse_ball(details.get("ball_on", ""))
        if not side or number is None or yards is None:
            return None
        return yards >= (100 - number if side == "OWN" else number)
    numeric_distance = parse_yards(distance)
    if yards is None or numeric_distance is None:
        return None
    return yards >= numeric_distance * {"1": 0.5, "2": 0.7}.get(down, 1.0)
