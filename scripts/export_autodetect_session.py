"""List or export correction data captured while editing detected clips."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tapesift.services.autodetect_export_service import (  # noqa: E402
    AutodetectExportError,
    build_correction_bundle,
    canonical_json,
    list_captured_sessions,
    open_project_readonly,
    write_correction_bundle,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export local TapeSift autodetect correction data.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser(
        "list", help="List captured sessions in a project.")
    list_parser.add_argument("--project", type=Path, required=True)

    batches_parser = subparsers.add_parser(
        "batches", help="List bounded review batches for one capture session.")
    batches_parser.add_argument("--project", type=Path, required=True)
    batches_parser.add_argument(
        "--session", default="latest",
        help="Session id, or 'latest' (default).")

    score_parser = subparsers.add_parser(
        "score", help="Print one scoped development-batch score as JSON.")
    score_parser.add_argument("--project", type=Path, required=True)
    score_parser.add_argument(
        "--session", default="latest",
        help="Session id, or 'latest' (default).")
    score_parser.add_argument(
        "--batch", default="latest-completed",
        help="Batch id, or 'latest-completed' (default).")

    export_parser = subparsers.add_parser(
        "export", help="Export one deterministic JSON correction bundle.")
    export_parser.add_argument("--project", type=Path, required=True)
    export_parser.add_argument(
        "--session", default="latest",
        help="Session id, or 'latest' (default).")
    export_parser.add_argument("--output", type=Path, required=True)
    export_parser.add_argument(
        "--include-local-paths", action="store_true",
        help="Include source/proxy paths. Paths are redacted by default.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        conn = open_project_readonly(args.project)
        try:
            if args.command == "list":
                sessions = list_captured_sessions(conn)
                if not sessions:
                    print("No captured autodetect sessions.")
                    return 0
                for session in sessions:
                    print(
                        f"{session['id']}  {session['created_at']}  "
                        f"{session['detector_version']}")
                return 0

            bundle = build_correction_bundle(
                conn,
                session_selector=args.session,
                include_local_paths=(
                    args.include_local_paths
                    if args.command == "export" else False
                ),
            )
            if args.command == "batches":
                batches = bundle.get("review_batches", [])
                if not batches:
                    print("No autodetect review batches.")
                    return 0
                for batch in batches:
                    print(
                        f"{batch['id']}  {batch['status']}  "
                        f"{batch['start_ms']}..{batch['end_ms']}  "
                        f"review_complete={batch['review_complete']}")
                return 0
            if args.command == "score":
                batches = bundle.get("review_batches", [])
                if args.batch == "latest-completed":
                    selected = next(
                        (
                            batch for batch in reversed(batches)
                            if batch["status"] == "completed"
                        ),
                        None,
                    )
                else:
                    selected = next(
                        (
                            batch for batch in batches
                            if batch["id"] == args.batch
                        ),
                        None,
                    )
                if selected is None:
                    raise AutodetectExportError(
                        f"Autodetect review batch not found: {args.batch}")
                if not selected[
                        "eligible_for_scoped_development_metrics"]:
                    raise AutodetectExportError(
                        "That review batch is not complete and scoreable.")
                score_export = {
                    "schema_version": "1.0",
                    "bundle_kind": "autodetect_development_batch_score",
                    "film_id": bundle["film_id"],
                    "session_id": selected["session_id"],
                    "batch_id": selected["id"],
                    "range": {
                        "start_ms": selected["start_ms"],
                        "end_ms": selected["end_ms"],
                    },
                    "completed_at": selected["completed_at"],
                    "detector": {
                        "id": bundle["session"]["detector_id"],
                        "version": bundle["session"]["detector_version"],
                        "provenance": bundle["session"]["provenance"],
                    },
                    "privacy_mode": bundle["privacy_mode"],
                    "correction_capture_sha256": bundle["capture_sha256"],
                    "development_score": selected["development_score"],
                }
                print(canonical_json(score_export), end="")
                return 0
        finally:
            conn.close()
        created = write_correction_bundle(args.output, bundle)
        print(
            f"{'Wrote' if created else 'Already identical'}: {args.output}")
        return 0
    except (AutodetectExportError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
