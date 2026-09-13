"""Render one contact-sheet image per play for vision-model run/pass testing.

Each play becomes a single tiled image of frames sampled evenly across the
clip. The clip holds the same play from every camera angle the cut-up
contains, so even sampling shows the play more than once rather than showing
contradictory information.

Deliberately no scorebug handling: these films are coaches' All-22 cut-ups
with no broadcast overlay, so there is no down-and-distance for a model to
read instead of the football. Verified on Alabama-Auburn, Miami-Louisville,
Notre Dame-Miami and Virginia-Stanford.

Sealed films are refused by path.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tapesift.research.run_pass_features import (  # noqa: E402
    reject_quarantined_input,
)

FFMPEG = ROOT / "vendor" / "ffmpeg" / "ffmpeg.exe"
SEALED_MARKERS = ("ohio state", "oklahoma")


def load_marked_snap_anchors(path: Path) -> dict[str, int]:
    """Return the frozen angle-1 snap mark for each reviewed clip."""
    report = json.loads(path.read_text(encoding="utf-8"))
    rows = report.get("lofo_candidate_ranker", {}).get("rows", [])
    return {
        str(row["clip_id"]): int(row["actual_snap_ms"])
        for row in rows
        if int(row["angle"]) == 1
    }


def load_temporal_anchors(paths: list[Path]) -> dict[str, int]:
    """Return label-blind first-angle motion onsets from cached features."""
    anchors: dict[str, int] = {}
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            angle = next((
                item for item in record["temporal_diagnostics"]["angles"]
                if int(item["angle"]) == 1
                and item.get("usable")
                and item.get("onset_seconds") is not None
            ), None)
            if angle is not None:
                anchors[str(record["clip_id"])] = int(record["start_ms"]) + round(
                    (float(angle["start_seconds"])
                     + float(angle["onset_seconds"])) * 1000
                )
    return anchors


def load_center_anchors(path: Path) -> dict[str, tuple[float, float]]:
    """Return leakage-safe angle-1 predictions for reviewed visible centers."""
    report = json.loads(path.read_text(encoding="utf-8"))
    rows = report.get("leave_one_game_out", {}).get("locator", {}).get("rows", [])
    return {
        str(row["key"][1]): (float(row["predicted_x"]),
                             float(row["predicted_y"]))
        for row in rows
        if int(row["key"][2]) == 1
    }


def build_strip(video: Path, start_ms: int, end_ms: int, out: Path,
                columns: int, rows: int, tile_width: int,
                window_fraction: float = 1.0,
                window_seconds: float = 0.0,
                skip_seconds: float = 0.0,
                motion_row: bool = False,
                crop_center: tuple[float, float] | None = None,
                crop_width: int = 960) -> bool:
    """Render one tiled contact sheet. Returns False if ffmpeg produced nothing.

    `window_fraction` below 1.0 samples only the front of the clip. Sampling
    evenly across a whole clip costs run recall badly on long films: measured
    over 195 plays, run recall fell from 58.3% on clips under 25s to 12.5% on
    clips over 45s, while pass recall held. A run is a ~3s event, so at one
    frame every five seconds it lands between samples and the play reads as a
    pass. The front of the clip holds the first camera angle, which is enough.

    `window_seconds` replaces the fraction with a fixed span, which is the
    better shape of the same idea. A fraction still scales with clip length:
    at 50%, a 45s clip covers 22s of film and a 20s clip covers 10s, so the
    same sixteen tiles mean different amounts of football per play. Measured
    on Qwen3-VL over 157 plays at 50%, run recall still fell 73.9% -> 50.0%
    from the shortest clips to the longest. A fixed window makes every sheet
    the same span regardless of how the cut-up was assembled.
    """
    if window_seconds > 0:
        start_ms = start_ms + int(skip_seconds * 1000)
        available = (end_ms - start_ms) / 1000.0
        duration = min(window_seconds, max(available, 0.5))
    else:
        duration = (end_ms - start_ms) / 1000.0 * window_fraction
    count = columns * rows
    # Nudge the sample rate so the last tile lands inside the clip rather than
    # past its end, where ffmpeg would pad the sheet with a repeated frame.
    fps = count / max(duration - 0.25, 0.5)

    out.parent.mkdir(parents=True, exist_ok=True)
    crop_filter = ""
    if crop_center is not None:
        crop_height = round(crop_width * 9 / 16)
        x, y = crop_center
        crop_filter = (
            f"crop=w='min(iw,{crop_width})':h='min(ih,{crop_height})':"
            f"x='max(0,min(iw-ow,iw*{x:.6f}-ow/2))':"
            f"y='max(0,min(ih-oh,ih*{y:.6f}-oh/2))',"
        )
    command = [
        str(FFMPEG), "-hide_banner", "-loglevel", "error",
        "-ss", f"{start_ms / 1000:.3f}",
        "-t", f"{duration:.3f}",
        "-i", str(video),
    ]
    if motion_row:
        if (columns, rows) != (4, 2):
            raise ValueError("--motion-row requires the 4x2 eight-frame grid")
        weights = " ".join(["1"] * 11)
        video_filter = (
            f"[0:v]{crop_filter}format=yuv420p,split=3[b][d][t];"
            f"[b]fps={fps:.6f},scale={tile_width}:-2,tile=4x2:nb_frames=8,"
            "setpts=PTS-STARTPTS[base];"
            "[d]fps=2,format=gray,tblend=all_mode=difference,"
            f"select=eq(n\\,1)+eq(n\\,5),scale={tile_width}:-2,format=yuv420p,"
            "tile=2x1:nb_frames=2,setpts=PTS-STARTPTS[diff];"
            "[t]fps=6,format=gray,tblend=all_mode=difference,"
            f"tmix=frames=11:weights='{weights}':scale=0.2,select=eq(n\\,11),"
            f"scale={tile_width}:-2,format=yuv420p,split=2[t1][t2];"
            "[t1][t2]hstack,setpts=PTS-STARTPTS[trail];"
            "[diff][trail]hstack[row];[base][row]vstack[out]"
        )
        command.extend(["-filter_complex", video_filter, "-map", "[out]"])
    else:
        command.extend([
            "-vf", f"{crop_filter}fps={fps:.6f},scale={tile_width}:-1,"
                   f"tile={columns}x{rows}"
        ])
    command.extend(["-frames:v", "1", "-q:v", "3", "-y", str(out)])
    subprocess.run(command, check=False, capture_output=True)
    return out.exists() and out.stat().st_size > 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels-dir", type=Path,
                        default=ROOT / "research" / "run_pass_working"
                        / "run-pass-player-temporal-v1" / "labels")
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "research" / "run_pass_working"
                        / "experiment-0" / "strips")
    parser.add_argument("--games", nargs="*", default=None,
                        help="game groups to render; default all")
    parser.add_argument("--clips", nargs="*", type=int, default=None,
                        help="optional clip numbers within the selected games")
    parser.add_argument("--window-seconds", type=float, default=0.0,
                        help="fixed span to sample instead of a fraction of "
                             "the clip; 0 keeps the fraction behaviour")
    parser.add_argument("--skip-seconds", type=float, default=0.0,
                        help="seconds of pre-roll to skip before the window")
    parser.add_argument("--snap-report", type=Path, default=None,
                        help="candidate-ranker report containing marked snaps; "
                             "renders only matching clips")
    parser.add_argument("--snap-shift-ms", type=int, default=0,
                        help="shift a marked snap window for timing ablation")
    parser.add_argument("--temporal-features", nargs="*", type=Path, default=[],
                        help="cached Temporal v2 JSONL files used as fallback "
                             "motion-onset anchors")
    parser.add_argument("--temporal-shift-ms", type=int, default=-2000,
                        help="offset applied to fallback motion-onset anchors")
    parser.add_argument("--temporal-window-seconds", type=float, default=5.5,
                        help="span rendered around fallback motion onsets")
    parser.add_argument("--motion-row", action="store_true",
                        help="append two difference tiles and a two-second "
                             "cumulative motion trail")
    parser.add_argument("--center-report", type=Path, default=None,
                        help="anchor-locator report; unmatched clips retain "
                             "the full frame")
    parser.add_argument("--crop-width", type=int, default=960,
                        help="source-pixel width of the center/backfield crop")
    parser.add_argument("--columns", type=int, default=3)
    parser.add_argument("--rows", type=int, default=3)
    parser.add_argument("--tile-width", type=int, default=480)
    parser.add_argument("--window-fraction", type=float, default=1.0,
                        help="sample only the first N of the clip (0.6 = first "
                             "60%%); tightens frame spacing on long clips")
    parser.add_argument("--limit", type=int, default=0,
                        help="render at most this many plays per game (0 = all)")
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args()
    args.labels_dir = reject_quarantined_input(args.labels_dir)
    snap_anchors = (load_marked_snap_anchors(args.snap_report)
                    if args.snap_report else {})
    temporal_anchors = load_temporal_anchors(args.temporal_features)
    anchors = {**temporal_anchors, **snap_anchors}
    center_anchors = (load_center_anchors(args.center_report)
                      if args.center_report else {})

    manifest = []
    failures = []
    for path in sorted(args.labels_dir.glob("*.jsonl")):
        group = path.stem
        if args.games and group not in args.games:
            continue
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        rows = [r for r in rows if str(r.get("label", "")).casefold() in ("run", "pass")]
        if args.clips:
            rows = [r for r in rows if int(r["clip_number"]) in args.clips]
        if args.snap_report or args.temporal_features:
            rows = [r for r in rows if str(r.get("clip_id")) in anchors]
        if args.limit:
            rows = rows[:args.limit]

        for record in rows:
            video = Path(record["source_video_path"])
            if any(marker in str(video).lower() for marker in SEALED_MARKERS):
                raise SystemExit(f"ABORT - sealed film referenced: {video}")
            if not video.exists():
                failures.append((group, record["clip_number"], "video missing"))
                continue

            clip_id = str(record.get("clip_id"))
            anchor_ms = anchors.get(clip_id)
            anchor_source = (
                "marked_snap" if clip_id in snap_anchors
                else "temporal_motion_onset" if clip_id in temporal_anchors
                else None
            )
            crop_center = center_anchors.get(str(record.get("clip_id")))
            render_start_ms, render_end_ms = record["start_ms"], record["end_ms"]
            window_fraction = args.window_fraction
            window_seconds = args.window_seconds
            skip_seconds = args.skip_seconds
            if anchor_ms is not None:
                if anchor_source == "marked_snap":
                    render_start_ms = anchor_ms - 500 + args.snap_shift_ms
                    render_end_ms = render_start_ms + 3500
                else:
                    render_start_ms = anchor_ms + args.temporal_shift_ms
                    render_end_ms = render_start_ms + round(
                        args.temporal_window_seconds * 1000)
                if (render_start_ms < record["start_ms"]
                        or render_end_ms > record["end_ms"]):
                    failures.append((group, record["clip_number"],
                                     "snap window crosses clip boundary"))
                    continue
                window_fraction, window_seconds, skip_seconds = 1.0, 0.0, 0.0

            out = args.output_dir / group / f"clip{record['clip_number']:03d}.jpg"
            if args.skip_existing and out.exists() and out.stat().st_size > 0:
                pass
            elif not build_strip(video, render_start_ms, render_end_ms, out,
                                 args.columns, args.rows, args.tile_width,
                                 window_fraction, window_seconds, skip_seconds,
                                 args.motion_row, crop_center, args.crop_width):
                failures.append((group, record["clip_number"], "ffmpeg produced nothing"))
                continue

            manifest.append({
                "game_group": group,
                "clip_number": record["clip_number"],
                "clip_id": record.get("clip_id"),
                "label": str(record["label"]).casefold(),
                "start_ms": record["start_ms"],
                "end_ms": record["end_ms"],
                "duration_s": round((record["end_ms"] - record["start_ms"]) / 1000, 2),
                "strip": str(out.relative_to(args.output_dir)).replace("\\", "/"),
                "grid": f"{args.columns}x{args.rows}",
                "window_fraction": args.window_fraction,
                "window_seconds": args.window_seconds,
                "skip_seconds": args.skip_seconds,
                "marked_snap_ms": (
                    anchor_ms if anchor_source == "marked_snap" else None),
                "temporal_anchor_ms": (
                    anchor_ms
                    if anchor_source == "temporal_motion_onset" else None),
                "anchor_source": anchor_source,
                "snap_shift_ms": (
                    args.snap_shift_ms
                    if anchor_source == "marked_snap" else None),
                "temporal_shift_ms": (
                    args.temporal_shift_ms
                    if anchor_source == "temporal_motion_onset" else None),
                "temporal_window_seconds": (
                    args.temporal_window_seconds
                    if anchor_source == "temporal_motion_onset" else None),
                "motion_row": args.motion_row,
                "crop_center": crop_center,
                "crop_width": args.crop_width if crop_center else None,
            })
            print(f"  {group} clip {record['clip_number']:>3} {record['label']:<5} -> {out.name}")

    manifest_path = args.output_dir / "strips-manifest.json"
    manifest_path.write_text(json.dumps({
        "grid": f"{args.columns}x{args.rows}",
        "tile_width": args.tile_width,
        "plays": len(manifest),
        "records": manifest,
    }, indent=2) + "\n", encoding="utf-8")

    print(f"\nrendered {len(manifest)} strips -> {manifest_path}")
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for group, clip, why in failures:
            print(f"  {group} clip {clip}: {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
