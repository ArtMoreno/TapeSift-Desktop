"""Export local human run/pass labels from a TapeSift project."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tapesift.research.run_pass_dataset import (  # noqa: E402
    export_project_run_pass_labels,
)


def parse_clip_range(value: str) -> set[int]:
    """Parse explicit clips and ranges, including intentional gaps."""
    selected: set[int] = set()
    for part in value.split(","):
        match = re.fullmatch(
            r"\s*(\d+)(?:\s*-\s*(\d+))?\s*", part)
        if not match:
            raise argparse.ArgumentTypeError(
                "Clip selection must look like 5-29 or 17-22,24-37"
            )
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < start:
            raise argparse.ArgumentTypeError(
                "Clip numbers must be positive and ranges must ascend"
            )
        selected.update(range(start, end + 1))
    return selected


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Export a local JSONL manifest for non-AI run/pass development."
        )
    )
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--cohort-id",
        default="",
        help=(
            "Stable ID for an intentionally reviewed research cohort. "
            "Requires --clip-range."
        ),
    )
    parser.add_argument(
        "--clip-range",
        type=parse_clip_range,
        help=(
            "Explicit approved database clip numbers, for example 5-29 or "
            "17-22,24-37. "
            "Requires --cohort-id."
        ),
    )
    parser.add_argument(
        "--include-derived",
        action="store_true",
        help="Include deterministic taxonomy labels as diagnostic rows.",
    )
    parser.add_argument(
        "--include-disabled",
        action="store_true",
        help="Include clips currently disabled for export.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing output manifest.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if bool(args.cohort_id.strip()) != (args.clip_range is not None):
        parser.error("--cohort-id and --clip-range must be supplied together")
    summary = export_project_run_pass_labels(
        args.project,
        args.output,
        research_cohort_id=args.cohort_id,
        approved_clip_numbers=args.clip_range,
        include_derived=args.include_derived,
        include_disabled=args.include_disabled,
        overwrite=args.force,
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
