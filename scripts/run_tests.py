"""Run every test file in its own process and retain every exit code and log."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".artifacts/tests"))
    parser.add_argument("--jobs", type=int, choices=range(1, 5), default=1)
    parser.add_argument("files", nargs="*", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    files = args.files or sorted((root / "tapesift/tests").glob("test_*.py"))
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONUNBUFFERED="1")
    def run(path):
        started = time.monotonic()
        log_path = output / (path.stem + ".log")
        command = [sys.executable, "-m", "pytest", str(path), "-q", "-p", "no:randomly",
                   "--tb=short", "-o", "faulthandler_timeout=90", "-p", "no:cacheprovider",
                   "--junitxml=" + str(output / (path.stem + ".xml"))]
        with log_path.open("w", encoding="utf-8") as stream:
            try:
                code = subprocess.run(command, cwd=root, env=environment,
                                      stdout=stream, stderr=subprocess.STDOUT, timeout=300).returncode
            except subprocess.TimeoutExpired:
                code = "timeout"
        return {"file": path.name, "exit": code, "seconds": round(time.monotonic()-started, 2)}
    results = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for future in as_completed([pool.submit(run, path) for path in files]):
            item = future.result()
            results.append(item)
            (output / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
            print(json.dumps(item), flush=True)
    return 0 if results and all(item["exit"] == 0 for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
