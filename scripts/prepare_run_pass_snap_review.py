"""Build label-blind snap-review queues for the frozen run/pass split."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEV_GAMES = {
    "sample_game_007",
    "sample_game_008",
    "kansas-o-vs-texas-tech-d",
    "miami-indiana",
    "miami-louisville",
    "mississippi-state-o-vs-ole-miss-d",
    "notre-dame-miami",
    "san-diego-state-o-vs-new-mexico-d",
}
CONFIRM_GAMES = {
    "alabama-auburn",
    "kentucky-o-vs-ole-miss-d",
    "lsu-o-vs-alabama-d",
    "miami-florida",
    "virginia-stanford",
}
SEALED_MARKERS = ("ohio state", "oklahoma")


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _project_clips(path: Path) -> dict[str, tuple[int, int]]:
    if not path.is_file():
        return {}
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        return {
            str(clip_id): (int(start_ms), int(end_ms))
            for clip_id, start_ms, end_ms in connection.execute(
                "SELECT id, start_ms, end_ms FROM clips"
            )
        }
    finally:
        connection.close()


def _snap_record(record: dict, cohort_id: str) -> dict:
    """Copy only localization inputs; run/pass truth cannot enter review."""
    return {
        "schema_version": "1.0",
        "dataset_kind": "tapesift_snap_localization_clips",
        "research_cohort_id": cohort_id,
        "research_approved": True,
        "split_role": "development",
        "project_name": str(record.get("project_name", "")),
        "source_video_path": str(record["source_video_path"]),
        "source_project": str(record.get("source_project", "")),
        "clip_id": str(record["clip_id"]),
        "clip_number": int(record["clip_number"]),
        "start_ms": int(record["start_ms"]),
        "end_ms": int(record["end_ms"]),
        "enabled": True,
        "label": "snap",
        "label_display": "Snap Localization",
        "label_source": "not_required",
        "label_valid": True,
        "trainable": True,
        "play_type": "",
        "play_action": "",
        "angle_starts_ms": [
            int(value) for value in record.get("angle_starts_ms", [])
        ],
        "angle_count": int(record.get("angle_count", 1)),
        "angle_source": str(record.get("angle_source", "")),
    }


def prepare_review_inputs(
    *,
    split: str,
    labels_dir: Path,
    strips_manifest: Path,
    snap_report: Path,
    output_dir: Path,
    overwrite: bool = False,
) -> dict:
    games = DEV_GAMES if split == "DEV" else CONFIRM_GAMES
    canonical = json.loads(strips_manifest.read_text(encoding="utf-8"))["records"]
    canonical = [row for row in canonical if row["game_group"] in games]
    labels = {
        (path.stem, str(row["clip_id"])): row
        for path in labels_dir.glob("*.jsonl")
        for row in _read_jsonl(path)
    }
    marked_rows = json.loads(snap_report.read_text(encoding="utf-8"))[
        "lofo_candidate_ranker"
    ]["rows"]
    exact_ids = {
        str(row["clip_id"])
        for row in marked_rows
        if int(row["angle"]) == 1
    }

    project_cache: dict[Path, dict[str, tuple[int, int]]] = {}
    by_game: dict[str, list[dict]] = defaultdict(list)
    inventory_rows = []
    for item in canonical:
        key = (str(item["game_group"]), str(item["clip_id"]))
        if key not in labels:
            raise ValueError(f"Missing authoritative label record: {key}")
        source = labels[key]
        video = Path(str(source["source_video_path"]))
        if any(marker in str(video).casefold() for marker in SEALED_MARKERS):
            raise ValueError(f"Sealed film referenced: {video}")
        project = Path(str(source.get("source_project", "")))
        if project not in project_cache:
            project_cache[project] = _project_clips(project)
        expected_range = (int(source["start_ms"]), int(source["end_ms"]))
        project_ready = project_cache[project].get(str(source["clip_id"])) == expected_range
        exact = str(source["clip_id"]) in exact_ids
        inventory_rows.append({
            "game_group": key[0],
            "clip_number": int(source["clip_number"]),
            "clip_id": key[1],
            "exact_mark_present": exact,
            "video_ready": video.is_file(),
            "project_review_ready": project_ready,
        })
        if not exact:
            cohort_id = f"run-pass-p1-{split.casefold()}-{key[0]}"
            by_game[key[0]].append(_snap_record(source, cohort_id))

    output_dir.mkdir(parents=True, exist_ok=True)
    for game, rows in sorted(by_game.items()):
        path = output_dir / f"{game}-snap-review-input.jsonl"
        if path.exists() and not overwrite:
            raise FileExistsError(path)
        path.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
        )

    exact = sum(row["exact_mark_present"] for row in inventory_rows)
    missing = len(inventory_rows) - exact
    inventory = {
        "schema_version": "1.0",
        "split": split,
        "plays": len(inventory_rows),
        "existing_exact_marks": exact,
        "missing_exact_marks": missing,
        "video_ready_missing": sum(
            row["video_ready"] and not row["exact_mark_present"]
            for row in inventory_rows
        ),
        "project_review_ready_missing": sum(
            row["project_review_ready"] and not row["exact_mark_present"]
            for row in inventory_rows
        ),
        "automatic_exact_marks": 0,
        "analyst_review_required": missing,
        "records": sorted(
            inventory_rows,
            key=lambda row: (row["game_group"], row["clip_number"]),
        ),
    }
    inventory_path = output_dir / "inventory.json"
    if inventory_path.exists() and not overwrite:
        raise FileExistsError(inventory_path)
    inventory_path.write_text(
        json.dumps(inventory, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return inventory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("DEV", "CONFIRM"), required=True)
    parser.add_argument(
        "--labels-dir",
        type=Path,
        default=ROOT / "research/run_pass_working/run-pass-player-temporal-v1/labels",
    )
    parser.add_argument(
        "--strips-manifest",
        type=Path,
        default=ROOT / "research/run_pass_working/experiment-0/full8/strips-manifest.json",
    )
    parser.add_argument(
        "--snap-report",
        type=Path,
        default=ROOT / "research/run_pass_working/multigame-snap-v1/candidate-ranker-report.json",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    result = prepare_review_inputs(
        split=args.split,
        labels_dir=args.labels_dir,
        strips_manifest=args.strips_manifest,
        snap_report=args.snap_report,
        output_dir=args.output_dir,
        overwrite=args.force,
    )
    print(json.dumps({key: value for key, value in result.items() if key != "records"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
