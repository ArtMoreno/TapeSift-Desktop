"""The game as one picture: every play, in order, coloured by who made it.

Zoomed all the way out, a game stops being a list and becomes a texture.
Colour is the primary player, so a glance answers questions a table makes
you count for - who the offence leaned on, and when it started leaning.

This module only assembles the numbers. Painting lives in
``tapesift.ui_v2.heatmap_render`` and writing files in
``tapesift.services.heatmap_export``, so the same data can come out as a
picture, a page, or a spreadsheet without three versions of the truth.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, replace

from tapesift.models.clip import Clip
from tapesift.services.football_vocab import lookup
from tapesift.services.player_name_service import (
    resolve_roster, resolved_key)
from tapesift.services.tag_style_service import canonical_tag_key, style_for_tag
from tapesift.services.result_service import legacy_result_key, result_key, split_results
from tapesift.ui_v2.tag_readout import OUTCOME_COLORS, PLAY_TYPE_COLORS, outcome_key
from tapesift.ui_v2.attribute_grid import (
    C_NO_PLAYER, PLAYER_COLORS, rank_player_colours)

#: Plays are logged with a quarter, but film runs past it - overtime and
#: anything unlabelled still has to land somewhere on the page.
QUARTER_ORDER = ("Q1", "Q2", "Q3", "Q4", "OT")
UNKNOWN_QUARTER = "?"


def _clean(value: object) -> str:
    return str(value or "").strip()


def _quarter_of(clip: Clip) -> str:
    raw = _clean(clip.details.get("quarter")).upper()
    regulation = re.fullmatch(r"(?:Q(?:UARTER)?\s*)?([1-4])(?:ST|ND|RD|TH)?(?:\s+QUARTER)?", raw)
    if regulation:
        return f"Q{regulation[1]}"
    overtime = re.fullmatch(r"(?:(\d+)\s*OT|OT\s*(\d*)|OVERTIME(?:\s*(\d+))?)", raw)
    if overtime:
        number = int(next((v for v in overtime.groups() if v), "1"))
        if number > 0:
            return "OT" if number == 1 else f"{number}OT"
    return UNKNOWN_QUARTER


def quarter_order(value: str) -> int:
    if value in QUARTER_ORDER:
        return QUARTER_ORDER.index(value)
    return 3 + int(value[:-2]) if re.fullmatch(r"[1-9]\d*OT", value) else 1000000


TYPE_COLOURS = {"Pass": PLAY_TYPE_COLORS["pass"], "Run": PLAY_TYPE_COLORS["run"],
                "Special": PLAY_TYPE_COLORS["special"], "No Play": PLAY_TYPE_COLORS["no_play"],
                "Other": "#87958d", "Unlogged": "#27312c", "Unresolved": "#bda069"}
LAYERS = {"run_pass": "Run / Pass", "results": "Results", "players": "Primary player"}


def type_category(raw: str) -> str:
    value = raw.strip().casefold()
    known = lookup("run_pass", raw)
    if known is not None:
        return known.category
    return {"other": "Other", "": "Unlogged"}.get(value, "Unresolved")


def result_colour(value: str, styles=None) -> str:
    colour = str(style_for_tag(value, styles)["color"])
    if not colour:
        key = legacy_result_key(value)
        colour = next((str(style_for_tag(name, styles)["color"])
                       for name in (styles or {})
                       if legacy_result_key(name.replace("_", " ")) == key
                       and style_for_tag(name, styles)["color"]), "")
    return colour or OUTCOME_COLORS[outcome_key(value)]


@dataclass(frozen=True)
class HeatmapPlay:
    """One cell. Everything needed to redraw it or explain it."""

    index: int
    clip_id: str
    clip_number: int
    start_ms: int
    end_ms: int
    quarter: str
    player: str
    player_key: str
    colour: str
    result: str
    title: str
    quarter_raw: str = ""
    run_pass: str = ""
    concept: str = ""
    type_category: str = "Unlogged"
    type_colour: str = TYPE_COLOURS["Unlogged"]
    results: tuple[str, ...] = ()
    result_colours: tuple[str, ...] = ()
    turnover: bool = False
    play_action: str = ""
    down_distance: str = ""
    ball_on: str = ""
    yards: str = ""
    action: str = ""
    other_players: str = ""
    offense_team_id: str = ""
    attack_direction: str = ""
    game_clock: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "clip_id": self.clip_id,
            "clip_number": self.clip_number,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "quarter": self.quarter,
            "player": self.player,
            "colour": self.colour,
            "result": self.result,
            "title": self.title,
            "quarter_raw": self.quarter_raw,
            "run_pass": self.run_pass,
            "concept": self.concept,
            "type_category": self.type_category,
            "type_colour": self.type_colour,
            "results": list(self.results),
            "result_colours": list(self.result_colours),
            "turnover": self.turnover,
            **{key: getattr(self, key) for key in (
                "play_action", "down_distance", "ball_on", "yards", "action",
                "other_players", "offense_team_id", "attack_direction", "game_clock")},
        }


@dataclass(frozen=True)
class PlayerWorkload:
    """A player's share of the game, and where in it they appeared."""

    name: str
    key: str
    colour: str
    snaps: int
    per_quarter: dict[str, int]
    #: Where this player's plays sit in the game, as 0..1 along the
    #: whole film. This is the "and when" the ranking alone cannot say.
    positions: tuple[float, ...]

    @property
    def busiest_quarter(self) -> str:
        if not self.per_quarter:
            return ""
        peak = max(self.per_quarter.values())
        if peak <= 0:
            return ""
        for quarter in sorted(self.per_quarter, key=quarter_order):
            if self.per_quarter.get(quarter, 0) == peak:
                return quarter
        return ""

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "colour": self.colour,
            "snaps": self.snaps,
            "per_quarter": dict(self.per_quarter),
            "busiest_quarter": self.busiest_quarter,
        }


@dataclass(frozen=True)
class HeatmapData:
    """Everything the page draws, and everything an export has to carry."""

    title: str
    subtitle: str
    plays: tuple[HeatmapPlay, ...]
    players: tuple[PlayerWorkload, ...]
    quarters: tuple[str, ...]
    quarter_counts: dict[str, int]
    film_ms: int
    unassigned: int
    generator: str = "TapeSift"
    layered: bool = False
    layers: tuple[str, ...] = tuple(LAYERS)
    quarter_layer: str = "run_pass"
    view: str = "Whole game"
    scope: str = ""
    source_clip_count: int = 0
    report_template: str = ""
    report_orientation: str = "landscape"
    report_player: str = ""
    report_player_name: str = ""
    social_text: tuple[tuple[str, str], ...] = ()
    report_slide: int = 1

    @property
    def play_count(self) -> int:
        return len(self.plays)

    def plays_in(self, quarter: str) -> tuple[HeatmapPlay, ...]:
        return tuple(p for p in self.plays if p.quarter == quarter)

    def as_dict(self) -> dict[str, object]:
        """The whole picture as data, so an export can carry its source."""
        return {
            "generator": self.generator,
            "title": self.title,
            "subtitle": self.subtitle,
            "play_count": self.play_count,
            "film_ms": self.film_ms,
            "unassigned": self.unassigned,
            "quarters": list(self.quarters),
            "quarter_counts": dict(self.quarter_counts),
            "players": [p.as_dict() for p in self.players],
            "plays": [p.as_dict() for p in self.plays],
            "layered": self.layered,
            "visible_layers": list(self.layers),
            "quarter_layer": self.quarter_layer,
            "view": self.view,
            "scope": self.scope,
            "source_clip_count": self.source_clip_count,
            "report_template": self.report_template,
            "report_orientation": self.report_orientation,
            "report_player": self.report_player,
            "report_player_name": self.report_player_name,
            "social_text": dict(self.social_text),
            "report_slide": self.report_slide,
            "type_counts": self.type_counts,
            "result_counts": self.result_counts,
            "turnover_count": self.turnover_count,
            "unlogged_results": sum(not p.results for p in self.plays),
            "player_count_meaning": "logged primary-player assignments",
        }

    @property
    def type_counts(self) -> dict[str, int]:
        counts = Counter(p.type_category for p in self.plays)
        return {key: counts[key] for key in TYPE_COLOURS}

    @property
    def result_counts(self) -> dict[str, int]:
        return dict(Counter(result_key(value) for play in self.plays for value in play.results))

    @property
    def turnover_count(self) -> int:
        return sum(p.turnover for p in self.plays)

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.as_dict(), indent=indent)


def build_heatmap(
        clips: list[Clip], *, title: str = "Game Heat Map",
        subtitle: str = "", layered: bool = False, scope: str = "",
        tag_styles: dict | None = None) -> HeatmapData:
    """Assemble the game picture from the clips as they were logged.

    Colours come from ``rank_player_colours`` rather than a second scheme,
    so a player is the same colour here as on the timeline. A picture that
    disagreed with the surface it was made from would be worse than none.
    """
    ordered = sorted(clips, key=lambda c: (c.start_ms, c.clip_number, c.id))
    colours = rank_player_colours(list(ordered))
    # One player however their name was typed, or the workload picture
    # this whole page exists to show is simply wrong.
    roster = resolve_roster(
        _clean(clip.details.get("player_name")) for clip in ordered)
    if layered:
        from tapesift.services.heatmap_palette import player_colour
        colours = {resolved_key(name, roster): player_colour(name, roster)
                   for name in roster.values()}

    plays: list[HeatmapPlay] = []
    names: dict[str, str] = {}
    for index, clip in enumerate(ordered):
        player = _clean(clip.details.get("player_name"))
        key = resolved_key(player, roster)
        # The roster decides the label; the key is what groups them.
        if key:
            names.setdefault(key, roster.get(canonical_tag_key(player),
                                             player))
        raw_type = _clean(clip.details.get("run_pass"))
        category = type_category(raw_type)
        results = tuple(split_results(clip.details.get("result", "")))
        plays.append(HeatmapPlay(
            index=index,
            clip_id=clip.id,
            clip_number=clip.clip_number,
            start_ms=clip.start_ms,
            end_ms=clip.end_ms,
            quarter=_quarter_of(clip),
            player=player,
            player_key=key,
            colour=colours.get(key, C_NO_PLAYER) if key else C_NO_PLAYER,
            result=_clean(clip.details.get("result")),
            title=clip.clip_title,
            quarter_raw=_clean(clip.details.get("quarter")),
            run_pass=raw_type,
            concept=_clean(clip.details.get("play_type")),
            type_category=category,
            type_colour=str(style_for_tag(category, tag_styles)["color"]) or (
                _report_type_colour(category) if layered else TYPE_COLOURS[category]),
            results=results,
            result_colours=tuple((_report_result_colour if layered else result_colour)(value, tag_styles)
                                 for value in results),
            turnover=any(result_key(v) in {"interception", "fumble lost"} for v in results),
            **{field: _clean(clip.details.get(field)) for field in (
                "play_action", "down_distance", "ball_on", "yards", "action",
                "other_players", "offense_team_id", "attack_direction", "game_clock")},
        ))

    counts: dict[str, int] = {}
    per_quarter: dict[str, dict[str, int]] = {}
    positions: dict[str, list[float]] = {}
    total = len(plays) or 1
    for play in plays:
        if not play.player_key:
            continue
        key = play.player_key
        counts[key] = counts.get(key, 0) + 1
        per_quarter.setdefault(key, {})
        per_quarter[key][play.quarter] = (
            per_quarter[key].get(play.quarter, 0) + 1)
        positions.setdefault(key, []).append(play.index / total)

    ranked = sorted(counts, key=lambda k: (-counts[k], names.get(k, k)))
    players = tuple(
        PlayerWorkload(
            name=names.get(key, key),
            key=key,
            colour=colours.get(key, C_NO_PLAYER),
            snaps=counts[key],
            per_quarter=dict(per_quarter.get(key, {})),
            positions=tuple(positions.get(key, ())),
        )
        for key in ranked
    )

    seen = sorted({p.quarter for p in plays}, key=quarter_order)
    quarter_counts = {
        q: sum(1 for p in plays if p.quarter == q) for q in seen}

    return HeatmapData(
        title=title,
        subtitle=subtitle,
        plays=tuple(plays),
        players=players,
        quarters=tuple(seen),
        quarter_counts=quarter_counts,
        film_ms=max((p.end_ms for p in plays), default=0),
        unassigned=sum(1 for p in plays if not p.player_key),
        layered=layered,
        scope=scope,
        source_clip_count=len(plays),
    )


def palette_size() -> int:
    """How many players get a colour of their own before any repeat."""
    return len(PLAYER_COLORS)


def _report_type_colour(category):
    from tapesift.services.heatmap_palette import TYPE_COLORS
    return TYPE_COLORS.get(category.casefold().replace(" ", "_"), TYPE_COLOURS[category])


def _report_result_colour(value, styles):
    from tapesift.services.heatmap_palette import result_colour as colour
    return colour(value, styles)


def select_heatmap(data: HeatmapData, *, quarter: str = "", layers=None,
                   quarter_layer: str = "run_pass", player_key: str = "") -> HeatmapData:
    """Project one frozen snapshot without reranking its player colours."""
    selected = tuple(data.layers if layers is None else layers)
    if len(set(selected)) != len(selected) or any(key not in LAYERS for key in selected):
        raise ValueError("Unknown or repeated heatmap layer")
    if quarter_layer not in LAYERS or (quarter and quarter not in data.quarters):
        raise ValueError("Unknown quarter or heatmap layer")
    plays = data.plays_in(quarter) if quarter else data.plays
    if player_key:
        if not any(p.key == player_key for p in data.players):
            raise ValueError("Unknown player")
        plays = tuple(p for p in plays if p.player_key == player_key)
    players = []
    for player in data.players:
        assignments = [(i, play) for i, play in enumerate(plays) if play.player_key == player.key]
        if assignments:
            players.append(replace(player, snaps=len(assignments),
                per_quarter=dict(Counter(p.quarter for _, p in assignments)),
                positions=tuple(i / len(plays) for i, _ in assignments)))
    players.sort(key=lambda p: (-p.snaps, p.name))
    quarters = tuple(sorted({p.quarter for p in plays}, key=quarter_order))
    return replace(data, plays=plays, players=tuple(players), quarters=quarters,
        quarter_counts=dict(Counter(p.quarter for p in plays)),
        unassigned=sum(not p.player_key for p in plays), layers=selected,
        quarter_layer=quarter_layer, view=quarter or "Whole game",
        subtitle=(f"{len(plays)} clips · {len(players)} logged players · "
                  f"{sum(not p.player_key for p in plays)} unassigned · "
                  f"{quarter or 'Whole game'} · saved snapshot") if data.layered else data.subtitle)
