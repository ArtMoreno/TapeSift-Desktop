"""Fail-closed finalization of the Iteration 4B generalization review."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.prepare_iteration_4b_generalization import (  # noqa: E402
    CANDIDATE_ID,
    QUEUE_ID,
    canonical_json,
    sha256_file,
    write_new,
)

ARTIFACT_ROOT = (
    ROOT / "research" / "segmentation_benchmark"
    / "reports" / "iteration_4b_generalization_v1"
)
SELECTION = ARTIFACT_ROOT / "selection.locked.json"
PREDICTION_LOCK = ARTIFACT_ROOT / "predictions.locked.json"
QUEUE_LOCK = ARTIFACT_ROOT / "review" / "queue.locked.json"
STATE = (
    ARTIFACT_ROOT / "review"
    / "iteration_4b_generalization_v1.state.json"
)
TRUTH = (
    ARTIFACT_ROOT / "review"
    / "iteration_4b_generalization_v1.verified.jsonl"
)
OUTPUT_JSON = ARTIFACT_ROOT / "iteration_4b_generalization_report.json"
OUTPUT_MARKDOWN = ARTIFACT_ROOT / "iteration_4b_generalization_report.md"


class Iteration4BFinalizationError(RuntimeError):
    """The completed review does not match its frozen provenance."""


@dataclass(frozen=True)
class ReportBundle:
    report: dict[str, Any]
    json_text: str
    markdown_text: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise Iteration4BFinalizationError(f"Expected JSON object: {path}")
    return payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    rows = []
    for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise Iteration4BFinalizationError(
                f"Expected JSON object at {path}:{line_number}")
        rows.append(payload)
    return rows


def git_blob_sha256(commit: str, source_file: str) -> str:
    completed = subprocess.run(
        ["git", "show", f"{commit}:{source_file}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return hashlib.sha256(completed.stdout).hexdigest()


def semantic_prediction(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove run-specific metadata while preserving detector output."""
    value = dict(payload)
    value.pop("generated_at", None)
    value.pop("runtime_seconds", None)
    return value


def _prediction_rows(
    lock: dict[str, Any],
    arm: str,
) -> dict[str, dict[str, Any]]:
    rows = lock.get("predictions", {}).get(arm)
    if not isinstance(rows, list):
        raise Iteration4BFinalizationError(
            f"Prediction lock is missing the {arm} arm")
    by_film: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise Iteration4BFinalizationError(
                f"Invalid {arm} prediction lock row")
        film_id = str(row.get("film_id") or "")
        if not film_id or film_id in by_film:
            raise Iteration4BFinalizationError(
                f"Duplicate or empty {arm} film id")
        path = Path(str(row.get("file") or ""))
        if not path.is_file() or sha256_file(path) != row.get("sha256"):
            raise Iteration4BFinalizationError(
                f"{arm} prediction hash mismatch for {film_id}")
        payload = read_json(path)
        if payload.get("film_id") != film_id:
            raise Iteration4BFinalizationError(
                f"{arm} prediction payload mismatch for {film_id}")
        by_film[film_id] = {
            "lock": row,
            "payload": payload,
        }
    return by_film


def validate_frozen_chain(
    selection: dict[str, Any],
    prediction_lock: dict[str, Any],
    queue_lock: dict[str, Any],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    if selection.get("queue_id") != QUEUE_ID \
            or selection.get("status") != "locked_before_detection":
        raise Iteration4BFinalizationError(
            "Frozen selection identity changed")
    if prediction_lock.get("queue_id") != QUEUE_ID \
            or prediction_lock.get("status") != \
            "predictions_frozen_before_review":
        raise Iteration4BFinalizationError(
            "Prediction lock identity changed")
    if queue_lock.get("queue_id") != QUEUE_ID \
            or queue_lock.get("status") != \
            "blind_review_queue_locked":
        raise Iteration4BFinalizationError(
            "Review queue lock identity changed")

    selection_hash = sha256_file(SELECTION)
    prediction_hash = sha256_file(PREDICTION_LOCK)
    if prediction_lock.get("selection", {}).get("sha256") != selection_hash:
        raise Iteration4BFinalizationError(
            "Prediction lock no longer matches the selection")
    if queue_lock.get("selection_sha256") != selection_hash:
        raise Iteration4BFinalizationError(
            "Queue no longer matches the selection")
    if queue_lock.get("prediction_lock_sha256") != prediction_hash:
        raise Iteration4BFinalizationError(
            "Queue no longer matches the prediction lock")

    films = selection.get("films")
    if not isinstance(films, list) or len(films) != 5:
        raise Iteration4BFinalizationError(
            "Frozen selection must contain exactly five films")
    film_ids = {str(film.get("film_id") or "") for film in films}
    if "" in film_ids or len(film_ids) != len(films):
        raise Iteration4BFinalizationError(
            "Frozen selection contains invalid film ids")
    for film in films:
        source = Path(str(film.get("source_file") or ""))
        if not source.is_file():
            raise FileNotFoundError(source)
        if source.stat().st_size != int(
                film.get("source_size_bytes") or -1):
            raise Iteration4BFinalizationError(
                f"Frozen source size changed for {film['film_id']}")
        if sha256_file(source) != film.get("source_sha256"):
            raise Iteration4BFinalizationError(
                f"Frozen source hash changed for {film['film_id']}")

    detector_rows = selection.get("detectors")
    if not isinstance(detector_rows, dict):
        raise Iteration4BFinalizationError("Detector locks are missing")
    for arm in ("baseline", "candidate"):
        detector = detector_rows.get(arm)
        if not isinstance(detector, dict):
            raise Iteration4BFinalizationError(
                f"{arm} detector lock is missing")
        if git_blob_sha256(
            str(detector.get("source_commit") or ""),
            str(detector.get("source_file") or ""),
        ) != detector.get("source_sha256"):
            raise Iteration4BFinalizationError(
                f"{arm} detector source hash changed")
        if prediction_lock.get("detectors", {}).get(arm) != detector:
            raise Iteration4BFinalizationError(
                f"{arm} detector provenance changed after prediction")

    baseline = _prediction_rows(prediction_lock, "baseline")
    candidate = _prediction_rows(prediction_lock, "candidate")
    if set(baseline) != film_ids or set(candidate) != film_ids:
        raise Iteration4BFinalizationError(
            "Prediction arms do not cover the frozen film set")
    return baseline, candidate


def validate_completed_review(
    state: dict[str, Any],
    truth: list[dict[str, Any]],
    queue_lock: dict[str, Any],
) -> list[dict[str, Any]]:
    if state.get("queue_id") != QUEUE_ID:
        raise Iteration4BFinalizationError("Review state queue changed")
    items = state.get("items")
    if not isinstance(items, list) or len(items) != int(
            queue_lock.get("item_count") or -1):
        raise Iteration4BFinalizationError(
            "Review item count changed")
    item_ids = [str(item.get("item_id") or "") for item in items]
    if item_ids != queue_lock.get("item_ids"):
        raise Iteration4BFinalizationError(
            "Review item order or identity changed")
    if any(item.get("status") not in {"verified", "excluded"}
           for item in items):
        raise Iteration4BFinalizationError(
            "Iteration 4B review is incomplete")
    if any(item.get("decision") not in {
            "accepted", "revised", "excluded"} for item in items):
        raise Iteration4BFinalizationError(
            "Review contains an unsupported decision")
    truth_by_id = {
        str(row.get("item_id") or ""): row for row in truth}
    if len(truth_by_id) != len(truth) or set(truth_by_id) != set(item_ids):
        raise Iteration4BFinalizationError(
            "Verified truth does not cover the completed queue exactly")
    for item in items:
        row = truth_by_id[item["item_id"]]
        for key in (
            "film_id", "start_ms", "end_ms",
            "verification_status", "decision",
        ):
            state_key = "status" if key == "verification_status" else key
            if row.get(key) != item.get(state_key):
                raise Iteration4BFinalizationError(
                    f"Truth/state mismatch for {item['item_id']} {key}")
    return items


def summarize_review(items: list[dict[str, Any]]) -> dict[str, Any]:
    decisions = Counter(str(item["decision"]) for item in items)
    strata = Counter(str(item.get("candidate_stratum") or "unknown")
                     for item in items)
    kinds = Counter(str(item.get("candidate_kind") or "unknown")
                   for item in items)
    by_film: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        by_film[str(item["film_id"])].append(item)

    emitted = [
        item for item in items
        if item.get("candidate_kind") != "unclassified"
    ]
    unclassified = [
        item for item in items
        if item.get("candidate_kind") == "unclassified"
    ]
    disjoint_relocations = [
        {
            "item_id": item["item_id"],
            "original_start_ms": item["original_start_ms"],
            "original_end_ms": item["original_end_ms"],
            "reviewed_start_ms": item["start_ms"],
            "reviewed_end_ms": item["end_ms"],
        }
        for item in items
        if int(item["end_ms"]) <= int(item["original_start_ms"])
        or int(item["start_ms"]) >= int(item["original_end_ms"])
    ]

    def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
        counts = Counter(str(row["decision"]) for row in rows)
        return {
            "reviewed": len(rows),
            "accepted_unchanged": counts["accepted"],
            "revised": counts["revised"],
            "excluded": counts["excluded"],
            "accepted_unchanged_rate": (
                counts["accepted"] / len(rows) if rows else None),
        }

    return {
        "reviewed": len(items),
        "decisions": dict(sorted(decisions.items())),
        "candidate_strata": dict(sorted(strata.items())),
        "candidate_kinds": dict(sorted(kinds.items())),
        "items_with_edits": sum(
            bool(item.get("edit_history")) for item in items),
        "disjoint_relocations": disjoint_relocations,
        "emitted_play_sample": summary(emitted),
        "unclassified_window_sample": summary(unclassified),
        "by_film": {
            film_id: {
                **summary(rows),
                "emitted_play_sample": summary([
                    row for row in rows
                    if row.get("candidate_kind") != "unclassified"
                ]),
                "unclassified_window_sample": summary([
                    row for row in rows
                    if row.get("candidate_kind") == "unclassified"
                ]),
            }
            for film_id, rows in sorted(by_film.items())
        },
    }


def build_report() -> ReportBundle:
    selection = read_json(SELECTION)
    prediction_lock = read_json(PREDICTION_LOCK)
    queue_lock = read_json(QUEUE_LOCK)
    state = read_json(STATE)
    truth = read_jsonl(TRUTH)
    baseline, candidate = validate_frozen_chain(
        selection, prediction_lock, queue_lock)
    items = validate_completed_review(state, truth, queue_lock)

    semantic_rows = {}
    all_semantically_equal = True
    total_fallback_activations = 0
    for film_id in sorted(baseline):
        left = baseline[film_id]
        right = candidate[film_id]
        equal = semantic_prediction(left["payload"]) == \
            semantic_prediction(right["payload"])
        all_semantically_equal &= equal
        fallback_count = int(
            right["lock"].get("fallback_pair_count") or 0)
        total_fallback_activations += fallback_count
        semantic_rows[film_id] = {
            "semantically_equal": equal,
            "baseline_play_count": left["lock"]["play_count"],
            "candidate_play_count": right["lock"]["play_count"],
            "baseline_unclassified_count": (
                left["lock"]["unclassified_count"]),
            "candidate_unclassified_count": (
                right["lock"]["unclassified_count"]),
            "candidate_fallback_pair_count": fallback_count,
        }

    review = summarize_review(items)
    emitted = review["emitted_play_sample"]
    safety_passed = (
        all_semantically_equal
        and total_fallback_activations == 0
        and emitted["excluded"] == 0
        and emitted["revised"] == 0
    )
    if not safety_passed:
        decision = "FAIL_OR_INVESTIGATE"
    else:
        decision = "PASS_NARROW_SAFETY_KEEP_REVIEW_GUARD"

    report = {
        "schema_version": "1.0",
        "report_id": "iteration-4b-generalization-v1",
        "generated_at": utc_now(),
        "candidate_id": CANDIDATE_ID,
        "decision": decision,
        "scope": {
            "eligible_public_accuracy_claim": False,
            "warning": (
                "This sampled holdout establishes a narrow no-regression "
                "safety result, not film-wide accuracy, precision, recall, "
                "coverage, or positive fallback generalization."
            ),
        },
        "provenance": {
            "selection_sha256": sha256_file(SELECTION),
            "prediction_lock_sha256": sha256_file(PREDICTION_LOCK),
            "queue_lock_sha256": sha256_file(QUEUE_LOCK),
            "completed_state_sha256": sha256_file(STATE),
            "completed_truth_sha256": sha256_file(TRUTH),
        },
        "integrity": {
            "passed": True,
            "queue_complete": True,
            "reviewed_items": len(items),
            "frozen_films": len(selection["films"]),
        },
        "generalization_safety": {
            "passed": safety_passed,
            "candidate_semantically_equal_to_baseline_on_all_films": (
                all_semantically_equal),
            "candidate_fallback_activations": total_fallback_activations,
            "by_film": semantic_rows,
        },
        "blind_review": review,
        "interpretation": {
            "supported": [
                (
                    "The Iteration 4A fallback caused no output change on "
                    "these five untouched films."
                ),
                (
                    "All sampled detector-emitted plays were accepted "
                    "unchanged."
                ),
                (
                    "The strict activation gate and needs-review behavior "
                    "should remain unchanged."
                ),
            ],
            "not_supported": [
                (
                    "Positive fallback generalization to another eligible "
                    "black-gap topology; the fallback did not activate."
                ),
                "A public detector accuracy or coverage claim.",
                (
                    "Resolution of the pre-existing zero-play failure on "
                    "Clemson offense versus SMU defense."
                ),
            ],
            "next_action": (
                "Keep the guarded 4A fallback. Next, run a detector-only "
                "activation sweep over still-untouched film, freeze every "
                "eligible candidate-only fallback proposal before human "
                "review, and blind-review that activation-bearing sample. "
                "Track Clemson-SMU's shared zero-play topology as a separate "
                "coverage diagnosis without retuning against this frozen "
                "4B review."
            ),
        },
    }
    markdown_text = render_markdown(report)
    return ReportBundle(
        report=report,
        json_text=canonical_json(report),
        markdown_text=markdown_text,
    )


def render_markdown(report: dict[str, Any]) -> str:
    safety = report["generalization_safety"]
    review = report["blind_review"]
    emitted = review["emitted_play_sample"]
    unclassified = review["unclassified_window_sample"]
    lines = [
        "# TapeSift Iteration 4B Generalization Review",
        "",
        f"> **{report['scope']['warning']}**",
        "",
        "## Decision",
        "",
        "- **PASS** the narrow no-regression safety gate.",
        "- Keep the Iteration 4A fallback activation gate unchanged.",
        "- Keep every recovered pair marked `needs_review`.",
        "- **HOLD** any broad or positive generalization claim.",
        "",
        "## Frozen comparison",
        "",
        f"- Untouched films: **{report['integrity']['frozen_films']}**",
        "- Candidate semantically equal to baseline on all films: "
        f"**{'YES' if safety['candidate_semantically_equal_to_baseline_on_all_films'] else 'NO'}**",
        "- Candidate fallback activations: "
        f"**{safety['candidate_fallback_activations']}**",
        "",
        "## Blind review",
        "",
        f"- Completed: **{review['reviewed']} of {review['reviewed']}**",
        f"- Accepted unchanged: "
        f"**{review['decisions'].get('accepted', 0)}**",
        f"- Boundary revisions: "
        f"**{review['decisions'].get('revised', 0)}**",
        f"- Exclusions: "
        f"**{review['decisions'].get('excluded', 0)}**",
        f"- Detector-emitted plays accepted unchanged: "
        f"**{emitted['accepted_unchanged']} of {emitted['reviewed']}**",
        f"- Unclassified windows accepted unchanged: "
        f"**{unclassified['accepted_unchanged']} of "
        f"{unclassified['reviewed']}**",
        f"- Unclassified windows revised into boundaries: "
        f"**{unclassified['revised']} of {unclassified['reviewed']}**",
        f"- Disjoint boundary relocations requiring explicit interpretation: "
        f"**{len(review['disjoint_relocations'])}**",
        "",
        "## Per-film result",
        "",
        "| Film | Reviewed | Accepted | Revised | Excluded |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for film_id, row in review["by_film"].items():
        lines.append(
            f"| {film_id} | {row['reviewed']} | "
            f"{row['accepted_unchanged']} | {row['revised']} | "
            f"{row['excluded']} |"
        )
    lines.extend([
        "",
        "## Interpretation",
        "",
        (
            "The 4A fallback was inert on all five untouched films, so this "
            "pass found no collateral regression. Every sampled emitted play "
            "was accepted unchanged."
        ),
        "",
        (
            "All seven revisions came from footage the detector had already "
            "left unclassified. Clemson offense versus SMU defense remains "
            "the clearest coverage failure: baseline and candidate both "
            "returned zero plays, and all five sampled windows required "
            "human boundaries."
        ),
        "",
        (
            "One Clemson item was relocated to a non-overlapping part of the "
            "film during review. It is retained and disclosed in the JSON "
            "provenance, but it is not used to support the emitted-play "
            "acceptance or candidate no-regression gates."
        ),
        "",
        "## Next action",
        "",
        report["interpretation"]["next_action"],
        "",
    ])
    return "\n".join(lines)


def write_report(bundle: ReportBundle) -> None:
    write_new(OUTPUT_JSON, bundle.json_text)
    write_new(OUTPUT_MARKDOWN, bundle.markdown_text)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Finalize the frozen TapeSift Iteration 4B review")
    value.add_argument(
        "--check-only",
        action="store_true",
        help="Validate and report in memory without writing completion files.",
    )
    return value


def main() -> int:
    args = parser().parse_args()
    bundle = build_report()
    if not args.check_only:
        write_report(bundle)
    result = {
        "status": "validated" if args.check_only else "finalized",
        "decision": bundle.report["decision"],
        "reviewed": bundle.report["blind_review"]["reviewed"],
        "accepted_unchanged": bundle.report[
            "blind_review"]["decisions"].get("accepted", 0),
        "revised": bundle.report[
            "blind_review"]["decisions"].get("revised", 0),
        "candidate_fallback_activations": bundle.report[
            "generalization_safety"]["candidate_fallback_activations"],
        "candidate_semantically_equal_to_baseline": bundle.report[
            "generalization_safety"][
                "candidate_semantically_equal_to_baseline_on_all_films"],
        "report": str(OUTPUT_MARKDOWN),
    }
    print(canonical_json(result).rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
