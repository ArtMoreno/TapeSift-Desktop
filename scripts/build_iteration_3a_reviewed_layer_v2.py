"""Build the timestamp-corrected v2 reviewer-verified correction layer.

This is a new artifact chain.  It reads the immutable v1 guided and boundary
inputs, but writes only v2 paths and never replaces the frozen v1 reviewed layer.
The approval timestamp is the original sidecar CreationTimeUtc.  It records
when the approval was captured, not the exact time of the owner's click.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_iteration_3a_reviewed_layer import (  # noqa: E402
    BOUNDARY_QA_QUEUE_SHA256,
    GUIDED_COMPLETION_LOCK_SHA256,
    GUIDED_IDS,
    GUIDED_BOUND_IDS,
    OWNER_RANGE_IDS,
    LockedFile,
    OwnerLayerSpec,
    build_owner_layer,
    write_owner_layer,
)


VERIFICATION_DIR = (
    ROOT / "research" / "segmentation_benchmark" / "verification")
REPORT_DIR = (
    ROOT / "research" / "segmentation_benchmark" / "reports"
    / "iteration_3a_holdout_v1")

GUIDED_COMPLETION_LOCK = (
    REPORT_DIR / "guided_final_five_v1.completion.lock.json")
REVIEW_APPROVAL = REPORT_DIR / "reviewer_verified_final_ranges_v2.json"
BOUNDARY_QA_QUEUE = (
    VERIFICATION_DIR / "iteration_3a_boundary_qa_v1.queue.locked.json")

OUTPUT_STATE = (
    VERIFICATION_DIR
    / "iteration_3a_reviewer_verified_final_five_v2.completed.raw.state.json")
OUTPUT_TRUTH = (
    VERIFICATION_DIR
    / "iteration_3a_reviewer_verified_final_five_v2.completed.raw.verified.jsonl")
OUTPUT_QUEUE = (
    VERIFICATION_DIR
    / "iteration_3a_reviewer_verified_final_five_v2.queue.locked.json")
OUTPUT_LOCK = (
    REPORT_DIR / "reviewer_verified_final_five_v2.completion.lock.json")

REVIEW_APPROVAL_SHA256 = (
    "6c074b9d2d1f6469e98470b618eba3596603c48689f9ae0ed6453a26ab3f14ae")
APPROVAL_RECORDED_AT = "2026-07-26T00:07:51Z"
APPROVAL_RECORDED_AT_BASIS = (
    "Original sidecar CreationTimeUtc; recorded approval time, "
    "not the exact click time."
)
OUTPUT_QUEUE_ID = (
    "segmentation-iteration-3a-holdout-v1-reviewer-verified-final-five-v2")
OUTPUT_LAYER_ID = "reviewer-verified-final-five-v2"


def default_spec() -> OwnerLayerSpec:
    return OwnerLayerSpec(
        root=ROOT,
        guided_completion_lock=LockedFile(
            GUIDED_COMPLETION_LOCK, GUIDED_COMPLETION_LOCK_SHA256),
        review_approval=LockedFile(
            REVIEW_APPROVAL, REVIEW_APPROVAL_SHA256),
        boundary_queue=LockedFile(
            BOUNDARY_QA_QUEUE, BOUNDARY_QA_QUEUE_SHA256),
        output_state=OUTPUT_STATE,
        output_truth=OUTPUT_TRUTH,
        output_queue=OUTPUT_QUEUE,
        output_lock=OUTPUT_LOCK,
        expected_ids=GUIDED_IDS,
        guided_bound_ids=GUIDED_BOUND_IDS,
        owner_range_ids=OWNER_RANGE_IDS,
        output_queue_id=OUTPUT_QUEUE_ID,
        output_layer_id=OUTPUT_LAYER_ID,
        required_approval_recorded_at=APPROVAL_RECORDED_AT,
        required_approval_recorded_at_basis=APPROVAL_RECORDED_AT_BASIS,
    )


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description=(
            "Build the timestamp-corrected v2 reviewer-verified Iteration 3A "
            "final-five layer"))
    value.add_argument(
        "--check-only",
        action="store_true",
        help="Validate and build bytes without writing v2 artifacts.",
    )
    return value


def main() -> int:
    args = parser().parse_args()
    spec = default_spec()
    bundle = build_owner_layer(spec)
    if not args.check_only:
        write_owner_layer(bundle)
    print(json.dumps({
        "status": "validated" if args.check_only else "frozen",
        "approval_recorded_at": APPROVAL_RECORDED_AT,
        "state_sha256": bundle.state_sha256,
        "truth_sha256": bundle.truth_sha256,
        "locked_queue_sha256": bundle.queue_sha256,
        "completion_lock": str(spec.output_lock),
        "completion_lock_sha256": bundle.completion_lock_sha256,
        "finalizer_argument": (
            f"{spec.output_lock}={bundle.completion_lock_sha256}"
        ),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
