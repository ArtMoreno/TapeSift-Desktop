"""Suggestions between adjacent film clips; the analyst confirms continuity."""
from dataclasses import dataclass, field

from tapesift.models.clip import Clip
from tapesift.services.football_context import FieldContext, down_distance, parse_down_distance, spot_label
from tapesift.services.football_vocab import category_for, parse_yards
from tapesift.services.result_service import legacy_result_key, split_results
from tapesift.services.play_field_service import describe_play


@dataclass
class DriveSuggestion:
    previous_gain: int | None = None
    current: dict[str, str] = field(default_factory=dict)
    message: str = "Enter Ball on here or Gain on the previous play."


def previous_clip(clips: list[Clip], current: Clip) -> Clip | None:
    ordered = sorted(clips, key=lambda clip: (clip.start_ms, clip.end_ms, clip.id))
    index = next((i for i, clip in enumerate(ordered) if clip.id == current.id), 0)
    if not index:
        return None
    previous = ordered[index - 1]
    if not previous.enabled or previous.end_ms > current.start_ms:
        return None
    return previous


def suggest(previous: Clip, current: dict[str, str]) -> DriveSuggestion:
    if current.get("drive_start") == "1":
        return DriveSuggestion(message="New drive · previous play is not linked.")
    before = previous.details
    for details in (before, current):
        results = split_results(details.get("result", ""))
        if details.get("run_pass", "").casefold() in {"special", "no play"} or any(
            category_for("result", result) in {"Penalty", "Special"}
            or legacy_result_key(result) in {"no play", "penalty", "punt", "kickoff", "field goal", "extra point"}
            for result in results
        ):
            return DriveSuggestion(message="Penalty / special play · enter the situation manually.")
    keys = {legacy_result_key(result) for result in split_results(before.get("result", ""))}
    if keys & {"touchdown", "safety", "interception", "fumble", "fumble lost", "fumble recovered", "turnover on downs"}:
        return DriveSuggestion(message="Score / possession change · confirm a new drive.")
    context = FieldContext.from_details(before, [])
    end = FieldContext.from_details(current, []).los_yards
    gain = parse_yards(before)
    if before.get("yards", "").strip() and gain is None:
        return DriveSuggestion(message="Check the previous play's Gain.")
    if context.los_yards is None:
        return DriveSuggestion(message="Set Ball on for the previous play first.")
    if end is None and current.get("ball_on", "").strip():
        return DriveSuggestion(message="Check Ball on; choose own or opponent territory.")
    inferred_gain = gain is None
    if gain is None and end is not None:
        gain = int(end - context.los_yards)
    if gain is None:
        return DriveSuggestion()
    expected = context.los_yards + gain
    if not 0 < expected < 100:
        return DriveSuggestion(message="Goal line reached · enter the next situation manually.")
    if end is not None and end != expected:
        return DriveSuggestion(message="Ball on differs from saved Gain · check the drive.")
    if keys & {"incompletion", "drop", "throwaway", "spike"}:
        valid = gain == 0 and not keys & {"completion", "reception"}
    else:
        valid = describe_play({**before, "yards": str(gain)}, []).end == expected
    if not valid:
        return DriveSuggestion(message="Result and yardage disagree · check the previous play.")
    down, _ = parse_down_distance(before.get("down_distance", ""))
    next_down = next_distance = ""
    if "first down" in keys:
        next_down, next_distance = "1", "Goal" if expected >= 90 else "10"
    elif context.to_gain_yards is not None and down:
        if expected >= context.to_gain_yards:
            next_down, next_distance = "1", "Goal" if expected >= 90 else "10"
        elif down == "4":
            return DriveSuggestion(message="Fourth down short of the line · check for a new drive.")
        else:
            next_down = str(int(down) + 1)
            next_distance = "Goal" if context.goal_to_go else str(int(context.to_gain_yards - expected))
    entered_down, entered_distance = parse_down_distance(current.get("down_distance", ""))
    if next_down and ((entered_down and entered_down != next_down)
                      or (entered_distance and entered_distance != next_distance)):
        return DriveSuggestion(message="Down / distance differs · check the drive or previous play.")
    updates = {}
    if end is None:
        updates["ball_on"] = "50" if expected == 50 else spot_label(expected)
    if next_down and (not entered_down or not entered_distance) and (
        not current.get("down_distance") or entered_down or entered_distance
    ):
        updates["down_distance"] = down_distance(next_down, next_distance)
    return DriveSuggestion(gain if inferred_gain else None, updates, "Confirm these are consecutive plays in the same drive.")
