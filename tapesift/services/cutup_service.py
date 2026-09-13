"""Read-only cutup selection and plans consumed by the existing export queue."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
import shutil

from tapesift.core.exceptions import ExportError
from tapesift.models.clip import Clip
from tapesift.models.project import Project
from tapesift.services import detail_service, export_service, filename_service, result_service
from tapesift.services.football_context import FieldContext, parse_down_distance

KINDS = {"player": "Players", "play_type": "Play types", "down": "Downs",
         "result": "Results", "action": "Actions", "quarter": "Quarters", "situation": "Situations"}


def film_order(clips):
    return sorted(clips, key=lambda c: (c.start_ms, c.end_ms, c.order_index, c.id))


def values_for_clip(clip: Clip, kind: str, *, primary_only=False, team_ids=()) -> list[str]:
    d = clip.details
    if kind == "player":
        names = detail_service.split_players(d.get("player_name", ""))
        if not primary_only:
            names += detail_service.split_players(d.get("other_players", ""))
            names += detail_service.split_players(d.get("quarterback", ""))
        return list(dict.fromkeys(" ".join(n.split()) for n in names if n.strip()))
    if kind in ("result", "action"):
        return result_service.split_results(d.get(kind, ""))
    if kind == "play_type":
        return list(dict.fromkeys(d[k] for k in ("run_pass", "play_type", "play_action") if d.get(k)))
    if kind == "down":
        down, _ = parse_down_distance(d.get("down_distance", ""))
        return [f"{down}{ {'1':'st', '2':'nd', '3':'rd', '4':'th'}.get(down, '')} down"] if down else []
    if kind == "quarter":
        value = d.get("quarter", "").upper().strip()
        return ["Q" + value if value.isdigit() else value] if value else []
    if kind == "situation":
        found = []
        down, distance = parse_down_distance(d.get("down_distance", ""))
        if down == "3" and distance.isdigit() and int(distance) >= 7:
            found.append("Third and long (7+)")
        context = FieldContext.from_details(d, list(team_ids))
        if context.los_yards is not None and 80 <= context.los_yards < 100:
            found.append("Red zone")
        return found
    raise ValueError(f"Unknown cutup grouping: {kind}")


def matches(clip, conditions, *, primary_only=False, team_ids=()):
    for kind, selected in conditions.items():
        if not selected:
            continue
        values = values_for_clip(clip, kind, primary_only=primary_only, team_ids=team_ids)
        key = result_service.result_key if kind == "result" else str.casefold
        if key(selected) not in {key(v) for v in values}:
            return False
    return True


@dataclass
class CutupGroup:
    kind: str
    value: str
    clip_ids: list[str]
    folder_name: str = ""
    excluded: set[str] = field(default_factory=set)


def catalog(clips, kind, conditions=None, *, primary_only=False, team_ids=()):
    groups = {}
    for clip in film_order(clips):
        if not clip.enabled or not matches(clip, conditions or {}, primary_only=primary_only, team_ids=team_ids):
            continue
        for value in values_for_clip(clip, kind, primary_only=primary_only, team_ids=team_ids):
            key = result_service.result_key(value) if kind == "result" else value.casefold()
            group = groups.setdefault(key, CutupGroup(kind, value, []))
            if clip.id not in group.clip_ids:
                group.clip_ids.append(clip.id)
    return sorted(groups.values(), key=lambda g: g.value.casefold())


def plan_cutups(project: Project, clips: list[Clip], groups: list[CutupGroup], *,
                mode="both", layout="game", game_name="", year=None, preset="source_quality",
                separator="hyphen", prepare=False):
    """Build ordinary clip/reel jobs; never modify project naming or clip details."""
    if layout not in ("game", "group"):
        raise ExportError("Choose game-first or player/group-first folders.")
    if not str(project.output_folder).strip():
        raise ExportError("Choose an export destination.")
    root = Path(project.output_folder)
    clean = filename_service.sanitize_filename_base
    game = clean(game_name or project.name)[:70] or "Game"
    year = str(project.game_year if year is None else year).strip()
    if year and (not year.isdigit() or len(year) != 4 or not 1800 <= int(year) <= 2199):
        raise ExportError("Enter a four-digit game year, or leave it unknown.")
    year = year or "Year-unknown"
    ordered = film_order(clips)
    numbers = {c.id: c.clip_number or i for i, c in enumerate(ordered, 1)}
    result = export_service.ExportPlan()
    folders = set()
    for group in groups:
        if group.kind not in KINDS:
            raise ExportError("Unknown cutup group.")
        selected_ids = set(group.clip_ids) - group.excluded
        selected = [deepcopy(c) for c in ordered if c.id in selected_ids and c.enabled]
        if not selected:
            continue
        for clip in selected:
            # The review checkboxes explicitly select reel membership without
            # changing the source clip's include_in_reel flag.
            clip.include_in_reel = True
            base = filename_service.effective_base(clip, separator)
            if clip.clip_number and base.startswith(f"{clip.clip_number:03d}_"):
                base = base[len(f"{clip.clip_number:03d}_"):]
            clip.output_filename_base = f"{numbers[clip.id]:03d}_{base}"
        label = clean(group.folder_name or group.value)[:80] or "Unnamed"
        if layout == "game":
            folder = root / f"{game}-{year}" / KINDS[group.kind] / label
        else:
            folder = root / KINDS[group.kind] / label / year / game
        # Sanitization can collapse two different names to the same folder.
        original = folder
        suffix = 2
        while str(folder).casefold() in folders:
            folder = original.with_name(original.name + f"-{suffix}")
            suffix += 1
        folders.add(str(folder).casefold())
        export_service._validate_output_directory(root, folder, prepare=prepare)
        snapshot = deepcopy(project)
        snapshot.name = label
        snapshot.output_folder = str(folder)
        snapshot.output_organization = "flat"
        snapshot.naming_template = "{clip_name}"
        plan = export_service.plan_export(snapshot, selected, mode, preset, True, separator, prepare=prepare)
        budget = min(180, 240 - len(str(folder)) - 5)
        if budget < 24:
            raise ExportError("The output path is too long. Shorten the destination, game name, or folder label.")
        taken = set()
        for job in plan.jobs:
            output = Path(job.output_path)
            job.output_path = str(filename_service.unique_path(folder, output.stem[:budget], ".mp4", taken))
            job.display_name = f"{group.value} · {job.display_name}"
        result.jobs.extend(plan.jobs)
        result.warnings.extend(plan.warnings)
        result.estimated_bytes += plan.estimated_bytes
    if prepare and result.jobs and result.estimated_bytes:
        free = shutil.disk_usage(root).free
        if free < result.estimated_bytes + export_service.LOW_DISK_MARGIN_BYTES:
            result.warnings.append(
                f"All selected groups may need ~{result.estimated_bytes // (1024 * 1024)} MB; "
                f"{free // (1024 * 1024)} MB is free on the output drive.")
    result.warnings = list(dict.fromkeys(result.warnings))
    return result
