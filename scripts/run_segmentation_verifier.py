"""Launch or prepare a research-only segmentation verification queue."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tapesift.core.config import AppSettings  # noqa: E402
from tapesift.research.segmentation_verification import (  # noqa: E402
    VerificationSession,
    build_pair_proposal_items,
    build_pilot_items,
)
from tapesift.research.segmentation_verifier_window import (  # noqa: E402
    SegmentationVerifierWindow,
)
from tapesift.ui_v2.fonts import load_v2_fonts  # noqa: E402
from tapesift.ui_v2.theme import stylesheet  # noqa: E402
from tapesift.services.play_detect_service import (  # noqa: E402
    BLACK_PAIR_REVIEW_REASON, SCENE_PAIR_REVIEW_REASON,
)

DEFAULT_MANIFEST = (
    ROOT / "research" / "segmentation_benchmark" / "manifest.local.json")
DEFAULT_VERIFICATION_DIR = (
    ROOT / "research" / "segmentation_benchmark" / "verification")
DEFAULT_STATE = DEFAULT_VERIFICATION_DIR / "pilot_50.state.json"
DEFAULT_TRUTH = DEFAULT_VERIFICATION_DIR / "pilot_50.verified.jsonl"
DEFAULT_FFPROBE = ROOT / "vendor" / "ffmpeg" / "ffprobe.exe"
PILOT_SELECTIONS = [
    ("bethune_cookman_o_vs_miami_d", 25),
    ("pittsburgh_o_vs_miami_d_2025", 25),
]
DEFAULT_QUEUE_ID = "segmentation-pilot-50-v1"


def parse_selection(value: str) -> tuple[str, int]:
    try:
        film_id, raw_count = value.rsplit("=", 1)
        count = int(raw_count)
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError(
            "Selection must use FILM_ID=COUNT") from exc
    if not film_id.strip() or count <= 0:
        raise argparse.ArgumentTypeError(
            "Selection needs a film id and a positive count")
    return film_id.strip(), count


def parse_strata(value: str) -> dict[str, int]:
    result: dict[str, int] = {}
    try:
        for entry in value.split(","):
            name, raw_count = entry.split("=", 1)
            name = name.strip()
            count = int(raw_count)
            if not name or count < 0 or name in result:
                raise ValueError
            result[name] = count
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError(
            "Strata must use NAME=COUNT comma-separated values") from exc
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify TapeSift segmentation predictions without "
                    "opening or changing a TapeSift project.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--truth", type=Path, default=DEFAULT_TRUTH)
    parser.add_argument("--ffprobe", type=Path, default=DEFAULT_FFPROBE)
    parser.add_argument("--queue-id", default=DEFAULT_QUEUE_ID)
    parser.add_argument(
        "--selection",
        action="append",
        type=parse_selection,
        metavar="FILM_ID=COUNT",
        help=(
            "Add a film and sample count. Repeat for a named queue. "
            "Defaults to the original 50-item pilot."),
    )
    parser.add_argument(
        "--strata",
        type=parse_strata,
        metavar="NAME=COUNT,...",
        help=(
            "Per-film quotas for confident, weak_recovered, other_review, "
            "unclassified, and scene_angle_pair candidates. Legacy names "
            "recovered and review remain accepted."),
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Create or inspect the verification queue, then exit.",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Open the UI briefly and close automatically.",
    )
    parser.add_argument(
        "--pair-prediction",
        type=Path,
        help="Build a blind queue from one Iteration 3A pair prediction.",
    )
    parser.add_argument(
        "--pair-sidecar",
        type=Path,
        help="Immutable JSON record of the blind pair selection.",
    )
    parser.add_argument(
        "--pair-baseline",
        type=Path,
        help="Optional baseline prediction used to record component ancestry.",
    )
    parser.add_argument(
        "--pair-reference-truth",
        type=Path,
        help="Optional reference truth hashed in the selection sidecar.",
    )
    parser.add_argument(
        "--pair-kind",
        choices=("scene_angle_pair", "black_gap_pair"),
        default="scene_angle_pair",
        help=(
            "Exact guarded pair proposal type to sample. The default keeps "
            "the original scene-angle review workflow."),
    )
    return parser.parse_args()


def load_or_create_session(args: argparse.Namespace) -> VerificationSession:
    state_path = args.state.resolve()
    truth_path = args.truth.resolve()
    if state_path.is_file():
        session = VerificationSession.load(state_path)
        if session.queue_id != args.queue_id:
            raise ValueError(
                f"Queue state belongs to {session.queue_id!r}, not "
                f"{args.queue_id!r}")
        if session.ground_truth_path.resolve() != truth_path:
            raise ValueError(
                "Queue state points to a different verified-output file: "
                f"{session.ground_truth_path}")
        return session
    if not args.manifest.is_file():
        raise FileNotFoundError(
            f"Benchmark manifest not found: {args.manifest}")
    if not args.ffprobe.is_file():
        raise FileNotFoundError(f"FFprobe not found: {args.ffprobe}")
    selections = args.selection or PILOT_SELECTIONS
    film_ids = [film_id for film_id, _count in selections]
    if len(set(film_ids)) != len(film_ids):
        raise ValueError("A film can appear only once in a verification queue")
    if args.pair_prediction:
        if len(selections) != 1:
            raise ValueError(
                "Pair review requires exactly one FILM_ID=COUNT selection")
        if args.strata is not None:
            raise ValueError("Pair review does not use general strata")
        film_id, count = selections[0]
        review_reason = (
            BLACK_PAIR_REVIEW_REASON
            if args.pair_kind == "black_gap_pair"
            else SCENE_PAIR_REVIEW_REASON
        )
        items, sidecar = build_pair_proposal_items(
            args.manifest,
            film_id,
            args.pair_prediction,
            args.ffprobe,
            count=count,
            review_reason=review_reason,
            baseline_prediction_path=args.pair_baseline,
            reference_truth_path=args.pair_reference_truth,
            detector_source_path=(
                ROOT / "tapesift" / "services" / "play_detect_service.py"),
            candidate_kind=args.pair_kind,
        )
        if args.pair_sidecar is None:
            raise ValueError("Pair review requires --pair-sidecar")
        sidecar_path = args.pair_sidecar.resolve()
        sidecar_text = (
            json.dumps(sidecar, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        )
        if sidecar_path.is_file():
            if sidecar_path.read_text(encoding="utf-8") != sidecar_text:
                raise ValueError(
                    "Existing pair selection sidecar does not match the "
                    "current blind selection")
        else:
            sidecar_path.parent.mkdir(parents=True, exist_ok=True)
            sidecar_path.write_text(sidecar_text, encoding="utf-8")
    else:
        items = build_pilot_items(
            args.manifest,
            selections,
            args.ffprobe,
            strata=args.strata,
        )
    expected_count = sum(count for _film_id, count in selections)
    if len(items) != expected_count:
        raise RuntimeError(
            f"Expected {expected_count} verification items but "
            f"built {len(items)}")
    return VerificationSession.create(
        state_path,
        truth_path,
        args.queue_id,
        items,
    )


def print_summary(session: VerificationSession) -> None:
    films = Counter(item.film_id for item in session.items)
    candidate_kinds = Counter(item.candidate_kind for item in session.items)
    candidate_strata = Counter(
        item.candidate_stratum or "legacy" for item in session.items)
    review_count = sum(
        item.detector_needs_review for item in session.items)
    print(f"Queue: {session.queue_id}")
    print(f"State: {session.state_path}")
    print(f"Verified output: {session.ground_truth_path}")
    print(f"Items: {len(session.items)}")
    for film_id, count in films.items():
        print(f"  {film_id}: {count}")
    print(f"Candidate types: {dict(candidate_kinds)}")
    print(f"Candidate strata: {dict(candidate_strata)}")
    print(f"Detector review flags: {review_count}")
    print(f"Completed: {session.completed_count}")


def main() -> int:
    args = parse_args()
    session = load_or_create_session(args)
    if args.prepare_only:
        print_summary(session)
        return 0

    app = QApplication(sys.argv[:1])
    app.setApplicationName("TapeSift Segmentation Verifier")
    app.setOrganizationName("TapeSift")
    load_v2_fonts()
    app.setStyleSheet(stylesheet())
    icon = ROOT / "tapesift" / "resources" / "icons" / "tapesift.ico"
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))

    settings = AppSettings()
    settings.scrub_proxy_enabled = False
    window = SegmentationVerifierWindow(
        session,
        settings,
        pair_mode=bool(args.pair_prediction),
    )
    window.show()
    if args.smoke:
        QTimer.singleShot(1_500, window.close)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
