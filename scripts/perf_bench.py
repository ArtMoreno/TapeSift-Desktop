"""Repeatable performance benchmark for the shipped V3 shell.

Two arms.  Both default to ``QT_QPA_PLATFORM=offscreen`` so the numbers are
about our code, not the compositor - but offscreen is much cheaper than a real
display for anything that rasterises, so ``startup --platform windows``
measures what a user actually waits for.  Compare like with like: a headed run
is not comparable to an offscreen baseline.

``startup``   spawns ``run_tapesift_v3.py`` N times as fresh processes and
              reads the perf JSON each run writes (see tapesift/core/perf.py).
              Reports the wall clock from spawn to first paint and the spans
              inside it.  The first run after a reboot is "cold"; the harness
              cannot force that, so it reports run 1 separately as well as the
              pooled warm runs.

``interact``  builds one MainWindowV3 in-process, opens a fixture project of
              M clips against a synthetic ffmpeg-generated source, and drives
              the hot paths users feel: clip-list rebuild, row selection,
              J/K/L, seek, timeline redraw, export kickoff.  Each operation is
              repeated and the distribution reported.

Every result is written as JSON under ``perf/`` so later runs can be diffed
rather than remembered.  Usage::

    ./.venv/Scripts/python.exe scripts/perf_bench.py startup  --runs 15
    ./.venv/Scripts/python.exe scripts/perf_bench.py startup  --runs 8 --platform windows
    ./.venv/Scripts/python.exe scripts/perf_bench.py startup-ab --a-root ../TapeSift-baseline --runs 3 --platform windows
    ./.venv/Scripts/python.exe scripts/perf_bench.py interact --reps 20 --clips 60
    ./.venv/Scripts/python.exe scripts/perf_bench.py compare perf/a.json perf/b.json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PERF_DIR = REPO / "perf"
PYTHON = sys.executable


# ---------------------------------------------------------------- statistics

def summarize(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    xs = sorted(values)
    n = len(xs)

    def pct(p: float) -> float:
        if n == 1:
            return xs[0]
        k = (n - 1) * p
        lo, hi = int(k), min(int(k) + 1, n - 1)
        return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)

    return {
        "n": n,
        "min": round(xs[0], 2),
        "median": round(statistics.median(xs), 2),
        "p90": round(pct(0.9), 2),
        "max": round(xs[-1], 2),
        "mean": round(statistics.fmean(xs), 2),
        "stdev": round(statistics.stdev(xs), 2) if n > 1 else 0.0,
    }


def _git_head(root: Path = REPO) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=root, text=True).strip()
    except Exception:
        return "unknown"


def _git_dirty(root: Path) -> bool:
    try:
        return bool(subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=root, text=True).strip())
    except Exception:
        return False


def _meta(arm: str, **extra) -> dict:
    return {
        "arm": arm,
        "git": _git_head(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        **extra,
    }


def _write(result: dict, out: Path | None) -> Path:
    PERF_DIR.mkdir(exist_ok=True)
    if out is None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out = PERF_DIR / f"{result['meta']['arm']}-{result['meta']['git']}-{stamp}.json"
    out.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return out


def _print_table(title: str, rows: dict[str, dict]) -> None:
    print(f"\n{title}")
    print(f"{'metric':<40}{'n':>4}{'median':>10}{'p90':>10}{'min':>10}{'max':>10}")
    for label, s in rows.items():
        if s.get("n", 0) == 0:
            continue
        print(f"{label:<40}{s['n']:>4}{s['median']:>10.1f}{s['p90']:>10.1f}"
              f"{s['min']:>10.1f}{s['max']:>10.1f}")


# ------------------------------------------------------------------ startup

def _headless_env(appdata: Path, perf_json: Path,
                  platform_name: str = "offscreen") -> dict:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = platform_name
    env["APPDATA"] = str(appdata)
    env["TAPESIFT_PERF_JSON"] = str(perf_json)
    env["TAPESIFT_PERF_EXIT_AT_FIRST_FRAME"] = "1"
    # Normal startup is bound by imports and construction, not by the two
    # optional ffmpeg version probes, so keep those off like the real launch.
    env.pop("TAPESIFT_FFMPEG_VERSION_CHECK", None)
    return env


def _seed_settings(appdata: Path) -> None:
    """A first launch shows a modal welcome dialog; the bench never should."""
    settings_dir = appdata / "TapeSift"
    settings_dir.mkdir(parents=True, exist_ok=True)
    settings = {"onboarding_seen": True}
    (settings_dir / "settings.json").write_text(json.dumps(settings), encoding="utf-8")


def _startup_run(root: Path, appdata: Path, perf_json: Path,
                 platform_name: str) -> dict | None:
    env = _headless_env(appdata, perf_json, platform_name)
    t0 = time.time()
    env["TAPESIFT_PERF_T0"] = repr(t0)
    proc = subprocess.run(
        [PYTHON, str(root / "run_tapesift_v3.py")],
        cwd=root, env=env, capture_output=True, text=True, timeout=120)
    wall = (time.time() - t0) * 1000.0
    if not perf_json.exists():
        print(f"no perf json (exit {proc.returncode})\n{proc.stderr[-2000:]}")
        return None
    records = json.loads(perf_json.read_text(encoding="utf-8"))
    return {
        "wall_ms": wall,
        "exit": proc.returncode,
        "marks": {r["label"]: r["ms"] for r in records
                  if r["kind"] == "mark"},
        "spans": {r["label"]: r["ms"] for r in records
                  if r["kind"] == "span"},
    }


def _startup_summary(per_run: list[dict]) -> dict[str, dict]:
    def series(getter) -> list[float]:
        values = []
        for run in per_run:
            try:
                values.append(getter(run))
            except KeyError:
                pass
        return values

    metrics = {
        "spawn_to_launcher_ms": series(lambda r: r["marks"]["launcher_entered"]),
        "imports_ms": series(
            lambda r: r["marks"]["imports_done"]
            - r["marks"]["launcher_entered"]),
        "spawn_to_first_frame_ms": series(
            lambda r: r["marks"]["first_frame"]),
        "spawn_to_window_shown_ms": series(
            lambda r: r["marks"]["window_shown"]),
    }
    for label in sorted({key for run in per_run for key in run["spans"]}):
        metrics[f"span:{label}"] = series(
            lambda run, name=label: run["spans"][name])
    return {key: summarize(values) for key, values in metrics.items()}


def bench_startup(runs: int, out: Path | None,
                  platform_name: str = "offscreen") -> Path:
    work = Path(tempfile.mkdtemp(prefix="tapesift-perf-startup-"))
    appdata = work / "appdata"
    _seed_settings(appdata)
    per_run: list[dict] = []
    for i in range(runs):
        run = _startup_run(
            REPO, appdata, work / f"run{i}.json", platform_name)
        if run is None:
            continue
        per_run.append(run)
        marks, spans = run["marks"], run["spans"]
        print(f"run {i:2d}: first_frame={marks.get('first_frame', float('nan')):8.1f} ms"
              f"  imports={marks.get('imports_done', float('nan')) - marks.get('launcher_entered', 0):7.1f}"
              f"  window={spans.get('app_v2_window_construct', float('nan')):7.1f}"
              f"  exit={run['exit']}")
    shutil.rmtree(work, ignore_errors=True)
    summary = _startup_summary(per_run)
    first = per_run[0] if per_run else {}
    result = {
        "meta": _meta("startup", runs=runs, first_run_is_cold_ish=True,
                      qpa_platform=platform_name),
        "summary_all_runs": summary,
        "first_run": first,
        "summary_warm_runs": _startup_summary(per_run[1:]),
        "runs": per_run,
    }
    _print_table("startup (all runs, ms)", summary)
    if len(per_run) > 1:
        _print_table("startup (runs 2..N, ms)", result["summary_warm_runs"])
    return _write(result, out)


def bench_startup_ab(a_root: Path, b_root: Path, runs: int,
                     out: Path | None, platform_name: str,
                     reference: Path | None = None) -> Path:
    work = Path(tempfile.mkdtemp(prefix="tapesift-perf-startup-ab-"))
    appdata = work / "appdata"
    _seed_settings(appdata)
    arms: dict[str, list[dict]] = {"A": [], "B": []}
    try:
        for index in range(runs):
            for arm, root in (("A", a_root.resolve()), ("B", b_root.resolve())):
                run = _startup_run(
                    root, appdata, work / f"{arm}-{index}.json",
                    platform_name)
                if run is None:
                    continue
                arms[arm].append(run)
                print(
                    f"{arm}{index + 1}: first_frame="
                    f"{run['marks']['first_frame']:8.1f} ms  "
                    f"window={run['spans']['app_v2_window_construct']:7.1f}  "
                    f"exit={run['exit']}")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    a_first = [run["marks"]["first_frame"] for run in arms["A"]]
    b_first = [run["marks"]["first_frame"] for run in arms["B"]]
    paired_gain = [a - b for a, b in zip(a_first, b_first)]
    result = {
        "meta": _meta(
            "startup-ab", runs=runs, order="A B A B A B",
            qpa_platform=platform_name, a_root=str(a_root.resolve()),
            a_git=_git_head(a_root), a_dirty=_git_dirty(a_root),
            b_root=str(b_root.resolve()), b_git=_git_head(b_root),
            b_dirty=_git_dirty(b_root), noise_floor_ms=31,
            reference=str(reference.resolve()) if reference else None),
        "arms": {
            arm: {"summary": _startup_summary(values), "runs": values}
            for arm, values in arms.items()
        },
        "paired_first_frame_gain_ms": {
            "summary": summarize(paired_gain), "values": paired_gain,
            "real": bool(paired_gain)
            and statistics.median(paired_gain) > 31,
        },
    }
    if reference is not None:
        baseline = json.loads(reference.read_text(encoding="utf-8"))
        result["reference_first_frame_ms"] = baseline[
            "summary_all_runs"]["spawn_to_first_frame_ms"]
    _print_table("startup A (ms)", result["arms"]["A"]["summary"])
    _print_table("startup B (ms)", result["arms"]["B"]["summary"])
    print("paired first-frame gain (A-B):", summarize(paired_gain))
    return _write(result, out)


# ----------------------------------------------------------------- interact

def _make_fixture_video(work: Path, seconds: int) -> Path:
    from tapesift.services import ffmpeg_service
    ffmpeg = ffmpeg_service.find_executable("ffmpeg", "")
    if not ffmpeg:
        raise SystemExit("ffmpeg not found; run scripts/fetch_ffmpeg.py")
    target = work / f"fixture_{seconds}s.mp4"
    cmd = [ffmpeg, "-y", "-loglevel", "error",
           "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=30:duration={seconds}",
           "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
           # The vendored ffmpeg is an LGPL build without libx264; openh264
           # is present there and plays back through Media Foundation.
           "-c:v", "libopenh264", "-b:v", "1500k", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-shortest", str(target)]
    subprocess.run(cmd, check=True, capture_output=True,
                   creationflags=ffmpeg_service.CREATE_NO_WINDOW)
    return target


def bench_interact(reps: int, clips: int, out: Path | None,
                   video_seconds: int = 120) -> Path:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    work = Path(tempfile.mkdtemp(prefix="tapesift-perf-interact-"))
    appdata = work / "appdata"
    _seed_settings(appdata)
    os.environ["APPDATA"] = str(appdata)
    perf_json = work / "interact-records.json"
    os.environ["TAPESIFT_PERF_JSON"] = str(perf_json)
    sys.path.insert(0, str(REPO))

    from tapesift.core import perf  # noqa: E402  (after env is set)
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from tapesift.core.config import AppSettings
    from tapesift.models.clip import Clip
    from tapesift.services import recovery_service
    from tapesift.services.ffprobe_service import probe_video
    from tapesift.services.project_service import ProjectSession
    from tapesift.ui_v3.main_window import MainWindowV3
    from tapesift.ui_v3.theme import stylesheet
    from tapesift.ui_v2.fonts import load_v2_fonts

    # Modal boxes would hang a headless run; answer Yes to everything.
    for name in ("warning", "information", "critical", "question"):
        setattr(QMessageBox, name,
                staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))

    app = QApplication.instance() or QApplication([])
    load_v2_fonts()

    timings: dict[str, list[float]] = {}

    def record(label: str, ms: float) -> None:
        timings.setdefault(label, []).append(ms)

    def timed(label: str, fn, *args, settle: bool = True):
        t = time.perf_counter()
        result = fn(*args)
        if settle:
            # Deliver posted events and paint, so the cost includes the
            # redraws the action triggered rather than only the Python call.
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
            app.sendPostedEvents()
        record(label, (time.perf_counter() - t) * 1000.0)
        return result

    def pump(ms: int) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    # -- style + window ------------------------------------------------------
    t = time.perf_counter(); sheet = stylesheet()
    record("stylesheet_build_ms", (time.perf_counter() - t) * 1000.0)
    t = time.perf_counter(); app.setStyleSheet(sheet)
    record("stylesheet_apply_ms", (time.perf_counter() - t) * 1000.0)

    settings = AppSettings.load()
    from tapesift.services import ffmpeg_service
    settings.ffmpeg_path = ffmpeg_service.find_executable("ffmpeg", "") or ""
    settings.ffprobe_path = ffmpeg_service.find_executable("ffprobe", "") or ""
    settings.default_project_folder = str(work)
    settings.default_output_folder = str(work / "out")
    settings.review_mode = False
    settings.save = lambda *a, **k: None  # never touch disk mid-bench
    recovery_service.mark_closed()

    t = time.perf_counter()
    win = MainWindowV3(settings)
    record("window_construct_ms", (time.perf_counter() - t) * 1000.0)
    win.resize(1600, 900)
    win.show()
    pump(200)

    # -- fixture project -----------------------------------------------------
    video = _make_fixture_video(work, video_seconds)
    meta = probe_video(settings.ffprobe_path, video)
    session = ProjectSession.create("Perf Fixture", work, work / "out")
    session.project.source_video_path = str(video)
    session.project.source_duration_ms = meta.duration_ms
    session.project.source_metadata = meta
    step = max(1000, (meta.duration_ms - 2000) // max(clips, 1))
    for i in range(clips):
        start = 500 + i * step
        session.add_clip(Clip(start_ms=start, end_ms=min(start + step - 200, meta.duration_ms),
                              clip_title=f"Play {i + 1:03d}",
                              tags=[("Run", "Pass")[i % 2]]))
    session.save()
    db_path = session.db_path
    session.close()

    # -- open project (repeated) -------------------------------------------
    for _ in range(max(3, reps // 4)):
        timed("open_project_ms", win._open_project, str(db_path))
        pump(300)  # let deferred open work (thumbnails etc.) land
        timed("close_project_ms", win._close_project)
        pump(50)
    win._open_project(str(db_path))
    pump(500)

    # -- interaction ---------------------------------------------------------
    n_rows = win.clip_list.table.rowCount() or clips
    for i in range(reps):
        timed("refresh_clip_list_ms", win._refresh_clip_list)
        timed("select_row_ms", win._select_row, i % max(n_rows, 1))
        timed("goto_clip_ms", win._goto_clip, 1 if i % 2 == 0 else -1)
        timed("seek_to_ms", win.player.seek_to, (i * 997) % meta.duration_ms)
        timed("shuttle_forward_ms", win.player.shuttle_forward)
        timed("shuttle_stop_ms", win.player.shuttle_stop)
        timed("shuttle_reverse_ms", win.player.shuttle_reverse)
        timed("shuttle_stop_ms", win.player.shuttle_stop)
        timed("timeline_invalidate_paint_ms",
              lambda: (win.player.slider._invalidate(), win.player.slider.repaint()))
        timed("timeline_resize_ms",
              lambda i=i: win.resize(1600 - (i % 2) * 40, 900))

    # -- export kickoff (start, measure, cancel) ----------------------------
    import tapesift.workers.export_worker as export_worker_mod

    class _NoRunExportWorker(export_worker_mod.ExportWorker):
        """Measure kickoff only: the thread starts, then exits at once."""

        def run(self):  # noqa: D401
            self.all_finished.emit()

    import tapesift.ui_core.main_window_workflow as wf
    original = wf.ExportWorker
    wf.ExportWorker = _NoRunExportWorker
    try:
        for _ in range(max(3, reps // 2)):
            timed("export_kickoff_ms", win._start_export,
                  "individual", settings.default_preset or "source_quality", False)
            pump(100)
            if win.export_worker is not None:
                win.export_worker.wait(2000)
                win.export_worker = None
    finally:
        wf.ExportWorker = original

    win._close_project()
    win.close()
    app.sendPostedEvents()
    perf.dump_json()
    # The temp dir is gone by interpreter exit; the atexit dump would only
    # log a FileNotFoundError.
    os.environ.pop("TAPESIFT_PERF_JSON", None)
    perf._JSON_PATH = ""

    records = json.loads(perf_json.read_text(encoding="utf-8")) if perf_json.exists() else []
    probes: dict[str, list[float]] = {}
    for r in records:
        probes.setdefault(f"{r['kind']}:{r['label']}", []).append(r["ms"])

    summary = {k: summarize(v) for k, v in timings.items()}
    probe_summary = {k: summarize(v) for k, v in sorted(probes.items())}
    result = {
        "meta": _meta("interact", reps=reps, clips=clips, video_seconds=video_seconds),
        "summary": summary,
        "in_app_probes": probe_summary,
        "raw": timings,
    }
    _print_table("interact - harness wall clock (ms, includes event delivery)", summary)
    _print_table("interact - in-app probes (ms)", probe_summary)
    shutil.rmtree(work, ignore_errors=True)
    return _write(result, out)


# ------------------------------------------------------------------ compare

def compare(a: Path, b: Path) -> None:
    ra = json.loads(a.read_text(encoding="utf-8"))
    rb = json.loads(b.read_text(encoding="utf-8"))
    for key in ("summary_warm_runs", "summary", "in_app_probes"):
        sa, sb = ra.get(key), rb.get(key)
        if not sa or not sb:
            continue
        print(f"\n{key}: {a.name} -> {b.name}")
        print(f"{'metric':<40}{'n':>6}{'median A':>11}{'median B':>11}{'delta':>9}{'p90 A':>10}{'p90 B':>10}")
        for label in sorted(set(sa) | set(sb)):
            x, y = sa.get(label, {}), sb.get(label, {})
            if not x.get("n") or not y.get("n"):
                continue
            d = y["median"] - x["median"]
            pct = (d / x["median"] * 100.0) if x["median"] else float("nan")
            print(f"{label:<40}{x['n']:>3}/{y['n']:<3}{x['median']:>10.1f}{y['median']:>11.1f}"
                  f"{pct:>+8.0f}%{x['p90']:>10.1f}{y['p90']:>10.1f}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("startup"); s.add_argument("--runs", type=int, default=12)
    # "offscreen" keeps the numbers about our code; "windows" measures what a
    # user actually waits for, which is materially slower.
    s.add_argument("--platform", default="offscreen")
    s.add_argument("--out", type=Path)
    ab = sub.add_parser("startup-ab")
    ab.add_argument("--a-root", type=Path, required=True)
    ab.add_argument("--b-root", type=Path, default=REPO)
    ab.add_argument("--runs", type=int, default=3)
    ab.add_argument("--platform", default="windows")
    ab.add_argument("--out", type=Path)
    ab.add_argument("--reference", type=Path)
    i = sub.add_parser("interact"); i.add_argument("--reps", type=int, default=20)
    i.add_argument("--clips", type=int, default=60); i.add_argument("--out", type=Path)
    i.add_argument("--video-seconds", type=int, default=120)
    c = sub.add_parser("compare"); c.add_argument("a", type=Path); c.add_argument("b", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "startup":
        print("wrote", bench_startup(args.runs, args.out, args.platform))
    elif args.cmd == "startup-ab":
        print("wrote", bench_startup_ab(
            args.a_root, args.b_root, args.runs, args.out, args.platform,
            args.reference))
    elif args.cmd == "interact":
        print("wrote", bench_interact(args.reps, args.clips, args.out, args.video_seconds))
    else:
        compare(args.a, args.b)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
