"""Measured play-summary geometry. Placement edits existing detail strings only."""
from __future__ import annotations

from dataclasses import dataclass

from tapesift.services.football_context import FieldContext, spot_label
from tapesift.services.football_vocab import category_for, parse_yards
from tapesift.services.result_service import legacy_result_key, split_results


@dataclass(frozen=True)
class PlayGeometry:
    context: FieldContext
    end: float | None = None
    catch: float | None = None
    event: float | None = None
    return_end: float | None = None
    enforced: float | None = None
    passing: bool = False
    flipped: bool = False
    hash: str = ""
    results: tuple[str, ...] = ()
    message: str = ""


def _spot(details: dict[str, str], key: str) -> float | None:
    value = parse_yards(details.get(key, ""))
    return float(value) if value is not None and 0 <= value <= 100 else None


def describe_play(details: dict[str, str], game_team_ids: list[str]) -> PlayGeometry:
    context_details = {**details, "offense_team_id": game_team_ids[0]} if game_team_ids else details
    context = FieldContext.from_details(context_details, game_team_ids)
    results = tuple(split_results(details.get("result", "")))
    keys = {legacy_result_key(item) for item in results}
    categories = {category_for("result", item) for item in results}
    complete = bool(keys & {"completion", "reception"})
    incomplete = bool(keys & {"incompletion", "drop", "throwaway", "spike"})
    turnover = bool(keys & {"interception", "fumble", "fumble lost", "fumble recovered"})
    no_play = "no play" in keys or details.get("run_pass", "").casefold() == "no play"
    special = ("Special" in categories or details.get("run_pass", "").casefold() == "special"
               or any(key.startswith(("field goal", "extra point")) for key in keys))
    penalty = "Penalty" in categories
    pending_penalty = penalty and ("penalty declined" not in keys or bool(
        keys & {"penalty accepted", "offsetting penalties"}))
    passing = complete or (details.get("run_pass", "").casefold() == "pass"
                           and "sack" not in keys)
    gain, yac = parse_yards(details), parse_yards(details.get("yac", ""))
    end = catch = None
    message = context.message
    if no_play:
        message = "No Play · credited movement withheld"
    elif complete and (incomplete or bool(keys & {"interception", "sack"})):
        message = "Check conflicting pass results"
    elif turnover:
        message = "Turnover/recovery: place event and return separately"
    elif pending_penalty:
        message = "Penalty: confirm enforced spot separately"
    elif special:
        message = "Special-teams outcome · movement not inferred"
    elif incomplete:
        message = "Incomplete · no catch or advance plotted"
    elif context.los_yards is not None and gain is not None:
        candidate = context.los_yards + gain
        negative = bool(keys & {"loss", "tfl"})
        if (not 0 <= candidate <= 100 or (negative and gain >= 0)
                or ("sack" in keys and gain > 0)
                or ("no gain" in keys and gain != 0)
                or ("touchdown" in keys and candidate != 100)
                or ("safety" in keys and candidate != 0)
                or ("first down" in keys and context.to_gain_yards is not None
                    and candidate < context.to_gain_yards)):
            message = "Check result / yardage"
        else:
            end = candidate
            if complete and yac is not None:
                # YAC may exceed gain for a screen caught behind scrimmage.
                candidate_catch = end - yac
                if 0 <= candidate_catch <= 100:
                    catch = candidate_catch
                else:
                    message = "Check YAC: catch falls outside the field"
    elif context.los_yards is not None and not message:
        message = "Set Gain to draw the measured finish"
    event = _spot(details, "field_event_spot") if turnover and not no_play else None
    return_end = (_spot(details, "field_return_end") if event is not None
                  and details.get("field_recovery_team") in {"offense", "defense"} else None)
    enforced = _spot(details, "field_enforced_spot") if penalty else None
    return PlayGeometry(context, end, catch, event, return_end, enforced, passing,
                        details.get("field_flip") == "1", details.get("field_hash", ""),
                        results, message)


def place(details: dict[str, str], mode: str, yard: int) -> dict[str, str]:
    """Return a draft; invalid or ambiguous placements leave it untouched."""
    updated = dict(details)
    if not 0 <= yard <= 100:
        raise ValueError("Choose a yard line between the goal lines")
    geometry = describe_play(details, [])
    if mode == "start":
        updated["ball_on"] = "50" if yard == 50 else spot_label(yard)
    elif mode == "finish":
        if geometry.context.los_yards is None:
            raise ValueError("Place the start first")
        keys = {legacy_result_key(item) for item in geometry.results}
        if details.get("run_pass", "").casefold() in {"no play", "special"} or keys & {
                   "interception", "fumble", "fumble lost", "fumble recovered", "no play",
                   "incompletion", "drop", "throwaway", "spike"}:
            raise ValueError("This result needs an event spot, not scrimmage Gain")
        if any(category_for("result", item) in {"Penalty", "Special"} for item in geometry.results):
            raise ValueError("Enter credited Gain separately; use Enforced for the penalty spot")
        updated["yards"] = str(int(yard - geometry.context.los_yards))
    elif mode == "catch":
        if geometry.end is None or not any(legacy_result_key(item) in {"completion", "reception"}
                                           for item in geometry.results):
            raise ValueError("Record a completion and valid Gain before placing the catch")
        updated["yac"] = str(int(geometry.end - yard))
    elif mode in {"event", "return", "enforced"}:
        key = {"event": "field_event_spot", "return": "field_return_end",
               "enforced": "field_enforced_spot"}[mode]
        if mode == "event" and not {legacy_result_key(item) for item in geometry.results} & {
                "interception", "fumble", "fumble lost", "fumble recovered"}:
            raise ValueError("Choose a turnover or fumble result before placing its event")
        if mode == "enforced" and not any(category_for("result", item) == "Penalty"
                                           for item in geometry.results):
            raise ValueError("Choose the penalty result before placing its enforced spot")
        if mode == "return" and (geometry.event is None or details.get("field_recovery_team")
                                 not in {"offense", "defense"}):
            raise ValueError("Place the event and choose the recovering team first")
        updated[key] = str(yard)
    else:
        raise ValueError("Unknown placement mode")
    return updated
