"""Label-blind human review state for Temporal v2 onset measurements.

The Temporal extractor's JSONL manifests contain football labels because
they will eventually feed classifier experiments.  A measurement-quality
review must not expose or copy those answers.  This module therefore builds
a deliberately smaller queue containing only clip identity, proposed angle
boundaries, proposed motion onsets, and the reviewer's judgments.

Review state is stored beside the research artifacts, never in a TapeSift
project database.  ``bind_project`` accepts an already-loaded clip snapshot;
it validates identity and boundaries without opening or mutating the project.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


STATE_SCHEMA_VERSION = "1.0"
STATE_DATASET_KIND = "tapesift_temporal_onset_review_state"
JUDGMENT_DATASET_KIND = "tapesift_temporal_onset_review_judgment"
CALIBRATION_DATASET_KIND = "tapesift_temporal_snap_calibration"
TEMPORAL_SCHEMA_VERSION = "2.1"

ANGLE_JUDGMENTS = frozenset({"correct", "early", "late", "unsure"})
SPLIT_JUDGMENTS = frozenset({"correct", "incorrect", "unsure"})
SNAP_STATUSES = frozenset({"marked", "not_visible", "unsure"})
REVIEW_WORKFLOWS = frozenset({"onset_judgment", "snap_calibration"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _milliseconds(seconds: object, *, field_name: str) -> int:
    """Convert manifest seconds to milliseconds without binary-float drift."""

    try:
        value = Decimal(str(seconds))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite number") from exc
    if not value.is_finite():
        raise ValueError(f"{field_name} must be a finite number")
    return int((value * 1000).to_integral_value(rounding=ROUND_HALF_UP))


def _number(value: object, *, field_name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite number") from exc
    if result != result or result in (float("inf"), float("-inf")):
        raise ValueError(f"{field_name} must be a finite number")
    return result


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _review_reasons(record: Mapping[str, Any]) -> list[str]:
    diagnostics = record.get("temporal_diagnostics")
    diagnostic_values = diagnostics if isinstance(diagnostics, dict) else {}
    reasons = [
        *_string_list(record.get("abstain_reasons")),
        *_string_list(diagnostic_values.get("abstain_reasons")),
    ]
    angles = diagnostic_values.get("angles")
    if isinstance(angles, list):
        for angle in angles:
            if isinstance(angle, dict):
                reasons.extend(_string_list(angle.get("eligibility_reasons")))
    unique: list[str] = []
    for reason in reasons:
        if reason not in unique:
            unique.append(reason)
    return unique


@dataclass(frozen=True)
class ManifestFingerprint:
    """Content identity for one Temporal manifest."""

    path: str
    name: str
    sha256: str

    def to_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "name": self.name,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ManifestFingerprint":
        return cls(
            path=str(payload.get("path", "")),
            name=str(payload.get("name", "")),
            sha256=str(payload.get("sha256", "")),
        )


@dataclass
class TemporalReviewAngle:
    """One proposed snap/onset within a camera angle."""

    angle: int
    # Seconds retain the terminology used by the extractor and are useful in
    # the UI.  ``start_seconds``/``end_seconds`` are clip-relative;
    # ``onset_seconds`` is relative to this angle's start.
    start_seconds: float
    end_seconds: float
    onset_seconds: float
    # Absolute source-film target used by the production player.
    proposed_onset_ms: int
    onset_confidence: float | None = None
    eligibility_reasons: list[str] = field(default_factory=list)
    judgment: str = ""
    reviewed_at: str = ""
    # Exact-snap calibration is deliberately separate from the coarse
    # early/correct/late judgment above.  A missing value with an empty
    # status means "not reviewed"; unavailable snaps are represented by a
    # status and no fabricated timestamp.
    actual_snap_ms: int | None = None
    snap_status: str = ""
    snap_reviewed_at: str = ""

    @property
    def range_start_clip_ms(self) -> int:
        return _milliseconds(
            self.start_seconds, field_name="angle start_seconds")

    @property
    def range_end_clip_ms(self) -> int:
        return _milliseconds(
            self.end_seconds, field_name="angle end_seconds")

    @property
    def proposed_onset_local_ms(self) -> int:
        return _milliseconds(
            self.onset_seconds, field_name="angle onset_seconds")

    @property
    def proposed_onset_source_ms(self) -> int:
        return self.proposed_onset_ms

    @property
    def snap_delta_ms(self) -> int | None:
        """Signed calibration error: actual snap minus proposed onset."""

        if self.actual_snap_ms is None:
            return None
        return self.actual_snap_ms - self.proposed_onset_ms

    @property
    def delta_ms(self) -> int | None:
        """Concise alias used by calibration reports and callers."""

        return self.snap_delta_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "angle": self.angle,
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "onset_seconds": self.onset_seconds,
            "proposed_onset_ms": self.proposed_onset_ms,
            "onset_confidence": self.onset_confidence,
            "eligibility_reasons": list(self.eligibility_reasons),
            "judgment": self.judgment,
            "reviewed_at": self.reviewed_at,
            "actual_snap_ms": self.actual_snap_ms,
            "snap_status": self.snap_status,
            "snap_reviewed_at": self.snap_reviewed_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TemporalReviewAngle":
        return cls(
            angle=int(payload["angle"]),
            start_seconds=_number(
                payload["start_seconds"], field_name="angle start_seconds"),
            end_seconds=_number(
                payload["end_seconds"], field_name="angle end_seconds"),
            onset_seconds=_number(
                payload["onset_seconds"], field_name="angle onset_seconds"),
            proposed_onset_ms=int(payload["proposed_onset_ms"]),
            onset_confidence=(
                None
                if payload.get("onset_confidence") is None
                else _number(
                    payload["onset_confidence"],
                    field_name="angle onset_confidence",
                )
            ),
            eligibility_reasons=_string_list(
                payload.get("eligibility_reasons")),
            judgment=str(payload.get("judgment", "")),
            reviewed_at=str(payload.get("reviewed_at", "")),
            actual_snap_ms=(
                None
                if payload.get("actual_snap_ms") is None
                else int(payload["actual_snap_ms"])
            ),
            snap_status=str(payload.get("snap_status", "")),
            snap_reviewed_at=str(payload.get("snap_reviewed_at", "")),
        )


@dataclass
class TemporalReviewSplit:
    """Optional judgment of the detected camera-angle transition."""

    left_clip_ms: int | None = None
    midpoint_clip_ms: int | None = None
    right_clip_ms: int | None = None
    judgment: str = ""
    reviewed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "left_clip_ms": self.left_clip_ms,
            "midpoint_clip_ms": self.midpoint_clip_ms,
            "right_clip_ms": self.right_clip_ms,
            "judgment": self.judgment,
            "reviewed_at": self.reviewed_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TemporalReviewSplit":
        def optional_int(key: str) -> int | None:
            value = payload.get(key)
            return None if value is None else int(value)

        return cls(
            left_clip_ms=optional_int("left_clip_ms"),
            midpoint_clip_ms=optional_int("midpoint_clip_ms"),
            right_clip_ms=optional_int("right_clip_ms"),
            judgment=str(payload.get("judgment", "")),
            reviewed_at=str(payload.get("reviewed_at", "")),
        )


@dataclass
class TemporalReviewItem:
    """A label-free review item derived from one Temporal v2.1 record."""

    item_id: str
    research_cohort_id: str
    project_name: str
    # The manifest path remains an audit reference.  Matching is performed by
    # ``project_file_name`` so Windows junction/redirect paths do not matter.
    project_path: str
    project_file_name: str
    source_video_path: str
    clip_id: str
    clip_number: int
    start_ms: int
    end_ms: int
    reasons: list[str]
    split: TemporalReviewSplit
    angles: list[TemporalReviewAngle]
    measurement_signature: str = ""
    completed_at: str = ""

    @property
    def clip_start_ms(self) -> int:
        return self.start_ms

    @property
    def clip_end_ms(self) -> int:
        return self.end_ms

    @property
    def complete(self) -> bool:
        # Temporal v2 expects two camera angles.  Requiring exactly two
        # explicit judgments keeps partial reviews out of the truth sidecar.
        return (
            len(self.angles) == 2
            and all(angle.judgment in ANGLE_JUDGMENTS for angle in self.angles)
        )

    @property
    def snap_complete(self) -> bool:
        """Whether every available view has an exact or unavailable result."""

        return (
            len(self.angles) in {1, 2}
            and all(
                (
                    angle.snap_status == "marked"
                    and angle.actual_snap_ms is not None
                )
                or (
                    angle.snap_status in {"not_visible", "unsure"}
                    and angle.actual_snap_ms is None
                )
                for angle in self.angles
            )
        )

    def angle(self, number: int) -> TemporalReviewAngle:
        match = next(
            (angle for angle in self.angles if angle.angle == number), None)
        if match is None:
            raise KeyError(
                f"Review item {self.item_id!r} has no angle {number}")
        return match

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "research_cohort_id": self.research_cohort_id,
            "project_name": self.project_name,
            "project_path": self.project_path,
            "project_file_name": self.project_file_name,
            "source_video_path": self.source_video_path,
            "clip_id": self.clip_id,
            "clip_number": self.clip_number,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "reasons": list(self.reasons),
            "split": self.split.to_dict(),
            "angles": [angle.to_dict() for angle in self.angles],
            "measurement_signature": self.measurement_signature,
            "status": "complete" if self.complete else "pending",
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TemporalReviewItem":
        split = payload.get("split")
        if not isinstance(split, dict):
            raise ValueError("Temporal review item has no split object")
        angles = payload.get("angles")
        if not isinstance(angles, list):
            raise ValueError("Temporal review item has no angle list")
        item = cls(
            item_id=str(payload["item_id"]),
            research_cohort_id=str(payload.get("research_cohort_id", "")),
            project_name=str(payload.get("project_name", "")),
            project_path=str(payload.get("project_path", "")),
            project_file_name=str(payload.get("project_file_name", "")),
            source_video_path=str(payload.get("source_video_path", "")),
            clip_id=str(payload["clip_id"]),
            clip_number=int(payload["clip_number"]),
            start_ms=int(payload["start_ms"]),
            end_ms=int(payload["end_ms"]),
            reasons=_string_list(payload.get("reasons")),
            split=TemporalReviewSplit.from_dict(split),
            angles=[
                TemporalReviewAngle.from_dict(angle)
                for angle in angles
                if isinstance(angle, dict)
            ],
            measurement_signature=str(
                payload.get("measurement_signature", "")),
            completed_at=str(payload.get("completed_at", "")),
        )
        _validate_judgments(item)
        return item


def _measurement_payload(item: TemporalReviewItem) -> dict[str, Any]:
    """Canonical, judgment-free measurement identity for stale-state checks."""

    return {
        "item_id": item.item_id,
        "research_cohort_id": item.research_cohort_id,
        "project_name": item.project_name,
        "project_file_name": item.project_file_name,
        "source_video_path": item.source_video_path,
        "clip_id": item.clip_id,
        "clip_number": item.clip_number,
        "start_ms": item.start_ms,
        "end_ms": item.end_ms,
        "reasons": list(item.reasons),
        "split": {
            "left_clip_ms": item.split.left_clip_ms,
            "midpoint_clip_ms": item.split.midpoint_clip_ms,
            "right_clip_ms": item.split.right_clip_ms,
        },
        "angles": [
            {
                "angle": angle.angle,
                "start_seconds": angle.start_seconds,
                "end_seconds": angle.end_seconds,
                "onset_seconds": angle.onset_seconds,
                "proposed_onset_ms": angle.proposed_onset_ms,
                "onset_confidence": angle.onset_confidence,
                "eligibility_reasons": list(angle.eligibility_reasons),
            }
            for angle in item.angles
        ],
    }


def _validate_measurement_signature(item: TemporalReviewItem) -> None:
    expected = _canonical_sha256(_measurement_payload(item))
    if not item.measurement_signature:
        raise ValueError(
            f"Review item {item.item_id!r} has no measurement signature")
    if item.measurement_signature != expected:
        raise ValueError(
            f"Review item {item.item_id!r} measurement signature is stale")


def _validate_judgments(item: TemporalReviewItem) -> None:
    if item.split.judgment and item.split.judgment not in SPLIT_JUDGMENTS:
        raise ValueError(
            f"Unsupported split judgment: {item.split.judgment!r}")
    for angle in item.angles:
        if angle.judgment and angle.judgment not in ANGLE_JUDGMENTS:
            raise ValueError(
                f"Unsupported angle judgment: {angle.judgment!r}")
        if angle.snap_status and angle.snap_status not in SNAP_STATUSES:
            raise ValueError(
                f"Unsupported snap status: {angle.snap_status!r}")
        if angle.snap_status == "marked" and angle.actual_snap_ms is None:
            raise ValueError(
                f"Angle {angle.angle} has a marked snap without a timestamp")
        if angle.snap_status in {"not_visible", "unsure"} and \
                angle.actual_snap_ms is not None:
            raise ValueError(
                f"Angle {angle.angle} has an unavailable snap with a timestamp")
        if not angle.snap_status and angle.actual_snap_ms is not None:
            raise ValueError(
                f"Angle {angle.angle} has a snap timestamp without a status")
        if angle.actual_snap_ms is not None:
            range_start_ms = item.start_ms + angle.range_start_clip_ms
            range_end_ms = item.start_ms + angle.range_end_clip_ms
            if not range_start_ms <= angle.actual_snap_ms <= range_end_ms:
                raise ValueError(
                    f"Angle {angle.angle} actual snap lies outside its "
                    "source range")


def _optional_manifest_ms(
        payload: Mapping[str, Any],
        key: str,
) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    return _milliseconds(value, field_name=f"transition {key}")


def _item_from_record(record: Mapping[str, Any]) -> TemporalReviewItem:
    if str(record.get("schema_version", "")) != TEMPORAL_SCHEMA_VERSION:
        raise ValueError(
            "Temporal review requires schema_version "
            f"{TEMPORAL_SCHEMA_VERSION}")
    diagnostics = record.get("temporal_diagnostics")
    if not isinstance(diagnostics, dict):
        raise ValueError("Temporal record has no temporal_diagnostics object")
    raw_angles = diagnostics.get("angles")
    if not isinstance(raw_angles, list):
        raise ValueError("Temporal record has no angle diagnostics")

    start_ms = int(record["start_ms"])
    end_ms = int(record["end_ms"])
    if start_ms < 0 or end_ms <= start_ms:
        raise ValueError("Temporal record has an invalid clip range")

    angles: list[TemporalReviewAngle] = []
    for index, raw_angle in enumerate(raw_angles, start=1):
        if not isinstance(raw_angle, dict):
            raise ValueError("Temporal record contains an invalid angle")
        angle_number = int(raw_angle.get("angle", index))
        start_seconds = _number(
            raw_angle.get("start_seconds"),
            field_name=f"angle {angle_number} start_seconds",
        )
        end_seconds = _number(
            raw_angle.get("end_seconds"),
            field_name=f"angle {angle_number} end_seconds",
        )
        onset_seconds = _number(
            raw_angle.get("onset_seconds"),
            field_name=f"angle {angle_number} onset_seconds",
        )
        if start_seconds < 0 or end_seconds <= start_seconds:
            raise ValueError(
                f"Angle {angle_number} has an invalid measured range")
        if onset_seconds < 0 or start_seconds + onset_seconds > end_seconds:
            raise ValueError(
                f"Angle {angle_number} onset lies outside its angle")
        target_clip_ms = _milliseconds(
            Decimal(str(start_seconds)) + Decimal(str(onset_seconds)),
            field_name=f"angle {angle_number} proposed onset",
        )
        confidence_value = raw_angle.get("onset_confidence")
        confidence = (
            None
            if confidence_value is None
            else _number(
                confidence_value,
                field_name=f"angle {angle_number} onset_confidence",
            )
        )
        angles.append(TemporalReviewAngle(
            angle=angle_number,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            onset_seconds=onset_seconds,
            proposed_onset_ms=start_ms + target_clip_ms,
            onset_confidence=confidence,
            eligibility_reasons=_string_list(
                raw_angle.get("eligibility_reasons")),
        ))
    angles.sort(key=lambda angle: angle.angle)

    transition = diagnostics.get("transition")
    transition_values = transition if isinstance(transition, dict) else {}
    project_path = str(record.get("source_project", ""))
    project_file_name = Path(project_path.replace("\\", "/")).name
    if not project_file_name:
        project_name = str(record.get("project_name", "")).strip()
        if project_name:
            project_file_name = f"{project_name}.tapesift"

    cohort = str(record.get("research_cohort_id", "")).strip()
    clip_id = str(record.get("clip_id", "")).strip()
    if not cohort or not clip_id:
        raise ValueError("Temporal record lacks cohort or clip identity")
    item = TemporalReviewItem(
        item_id=f"{cohort}:{clip_id}",
        research_cohort_id=cohort,
        project_name=str(record.get("project_name", "")),
        project_path=project_path,
        project_file_name=project_file_name,
        source_video_path=str(record.get("source_video_path", "")),
        clip_id=clip_id,
        clip_number=int(record["clip_number"]),
        start_ms=start_ms,
        end_ms=end_ms,
        reasons=_review_reasons(record),
        split=TemporalReviewSplit(
            left_clip_ms=_optional_manifest_ms(
                transition_values, "left_pulse_seconds"),
            midpoint_clip_ms=_optional_manifest_ms(
                transition_values, "midpoint_seconds"),
            right_clip_ms=_optional_manifest_ms(
                transition_values, "right_pulse_seconds"),
        ),
        angles=angles,
    )
    item.measurement_signature = _canonical_sha256(
        _measurement_payload(item))
    return item


def _load_manifest_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path}:{line_number}: invalid JSON: {exc.msg}"
                ) from exc
            if not isinstance(record, dict):
                raise ValueError(
                    f"{path}:{line_number}: expected a JSON object")
            records.append(record)
    return records


def _manifest_inputs(
        manifest_paths: Sequence[Path],
        *,
        only_flagged: bool,
) -> tuple[list[ManifestFingerprint], list[TemporalReviewItem]]:
    if not manifest_paths:
        raise ValueError("At least one Temporal manifest is required")
    fingerprints: list[ManifestFingerprint] = []
    items: list[TemporalReviewItem] = []
    seen_ids: set[str] = set()
    for requested_path in manifest_paths:
        path = Path(requested_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        fingerprints.append(ManifestFingerprint(
            path=str(path),
            name=path.name,
            sha256=_sha256(path),
        ))
        for record in _load_manifest_records(path):
            if only_flagged and record.get("classifier_eligible") is True:
                continue
            item = _item_from_record(record)
            if item.item_id in seen_ids:
                raise ValueError(
                    f"Duplicate Temporal review item: {item.item_id}")
            seen_ids.add(item.item_id)
            items.append(item)
    return fingerprints, items


def _fingerprint_identity(
        fingerprints: Iterable[ManifestFingerprint],
) -> list[tuple[str, str]]:
    # Path aliases and repository moves are harmless; file name plus content
    # hash still identifies the exact manifest supplied to the review.
    return sorted(
        (fingerprint.name.casefold(), fingerprint.sha256)
        for fingerprint in fingerprints
    )


def _review_workflow(value: object) -> str:
    workflow = str(value or "onset_judgment").strip().casefold()
    if workflow not in REVIEW_WORKFLOWS:
        raise ValueError(f"Unsupported Temporal review workflow: {value!r}")
    return workflow


class TemporalReviewSession:
    """Mutable, immediately persisted Temporal onset-review queue."""

    def __init__(
        self,
        state_path: Path,
        judgments_path: Path,
        review_id: str,
        manifests: list[ManifestFingerprint],
        items: list[TemporalReviewItem],
        *,
        current_index: int = 0,
        created_at: str = "",
        only_flagged: bool = True,
        workflow: str = "onset_judgment",
    ) -> None:
        self.state_path = Path(state_path)
        self.judgments_path = Path(judgments_path)
        self.review_id = review_id
        self.manifests = manifests
        self.items = items
        self._current_index = max(
            0, min(current_index, max(0, len(items) - 1)))
        self.created_at = created_at or _utc_now()
        self.updated_at = self.created_at
        self.only_flagged = bool(only_flagged)
        self.workflow = _review_workflow(workflow)

    @classmethod
    def create(
        cls,
        manifest_paths: Sequence[Path],
        state_path: Path,
        judgments_path: Path,
        *,
        review_id: str = "temporal-v2.1-onset-review",
        only_flagged: bool = True,
        workflow: str = "onset_judgment",
    ) -> "TemporalReviewSession":
        manifests, items = _manifest_inputs(
            manifest_paths, only_flagged=only_flagged)
        session = cls(
            state_path,
            judgments_path,
            review_id,
            manifests,
            items,
            only_flagged=only_flagged,
            workflow=workflow,
        )
        session.save()
        return session

    @classmethod
    def load(
        cls,
        state_path: Path,
        manifest_paths: Sequence[Path],
        judgments_path: Path | None = None,
        *,
        workflow: str | None = None,
    ) -> "TemporalReviewSession":
        state_path = Path(state_path)
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != STATE_SCHEMA_VERSION:
            raise ValueError(
                "Unsupported Temporal review state schema: "
                f"{payload.get('schema_version')!r}")
        if payload.get("dataset_kind") != STATE_DATASET_KIND:
            raise ValueError("File is not a Temporal onset-review state")

        only_flagged = payload.get("only_flagged") is not False
        stored_workflow = _review_workflow(
            payload.get("workflow", "onset_judgment"))
        active_workflow = (
            stored_workflow
            if workflow is None
            else _review_workflow(workflow)
        )
        current_manifests, expected_items = _manifest_inputs(
            manifest_paths, only_flagged=only_flagged)
        stored_manifest_payloads = payload.get("source_manifests")
        if not isinstance(stored_manifest_payloads, list):
            raise ValueError("Temporal review state has no manifest hashes")
        stored_manifests = [
            ManifestFingerprint.from_dict(value)
            for value in stored_manifest_payloads
            if isinstance(value, dict)
        ]
        if _fingerprint_identity(stored_manifests) != \
                _fingerprint_identity(current_manifests):
            raise ValueError(
                "Temporal review state is stale: a source manifest changed")

        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            raise ValueError("Temporal review state has no item list")
        stored_items = [
            TemporalReviewItem.from_dict(value)
            for value in raw_items
            if isinstance(value, dict)
        ]
        for item in stored_items:
            _validate_measurement_signature(item)

        expected_signatures = {
            item.item_id: item.measurement_signature
            for item in expected_items
        }
        stored_signatures = {
            item.item_id: item.measurement_signature
            for item in stored_items
        }
        if stored_signatures != expected_signatures:
            raise ValueError(
                "Temporal review state is stale: measured items changed")

        cursor_item_id = str(payload.get("cursor_item_id", ""))
        cursor = next(
            (
                index
                for index, item in enumerate(stored_items)
                if item.item_id == cursor_item_id
            ),
            int(payload.get("current_index", 0)),
        )
        output_path = (
            Path(judgments_path)
            if judgments_path is not None
            else Path(str(payload.get("judgments_path", "")))
        )
        if not str(output_path):
            raise ValueError("Temporal review state has no judgments path")
        session = cls(
            state_path,
            output_path,
            str(payload.get("review_id", "temporal-v2.1-onset-review")),
            current_manifests,
            stored_items,
            current_index=cursor,
            created_at=str(payload.get("created_at", "")),
            only_flagged=only_flagged,
            workflow=active_workflow,
        )
        session.updated_at = str(
            payload.get("updated_at", session.created_at))
        return session

    @classmethod
    def load_or_create(
        cls,
        manifest_paths: Sequence[Path],
        state_path: Path,
        judgments_path: Path,
        *,
        review_id: str = "temporal-v2.1-onset-review",
        only_flagged: bool = True,
        workflow: str | None = None,
    ) -> "TemporalReviewSession":
        if Path(state_path).is_file():
            session = cls.load(
                state_path,
                manifest_paths,
                judgments_path,
                workflow=workflow,
            )
            # Persist an explicitly selected workflow so a subsequent plain
            # load resumes the same task.
            if workflow is not None:
                session.save()
            return session
        return cls.create(
            manifest_paths,
            state_path,
            judgments_path,
            review_id=review_id,
            only_flagged=only_flagged,
            workflow=workflow or "onset_judgment",
        )

    @property
    def current_index(self) -> int:
        return self._current_index

    @property
    def current(self) -> TemporalReviewItem | None:
        return self.items[self._current_index] if self.items else None

    def item_complete(
        self,
        item: TemporalReviewItem | str,
    ) -> bool:
        """Return completion using the session's active review workflow."""

        reviewed = self.review_for(item) if isinstance(item, str) else item
        if self.workflow == "snap_calibration":
            return reviewed.snap_complete
        return reviewed.complete

    @property
    def completed_count(self) -> int:
        return sum(self.item_complete(item) for item in self.items)

    @property
    def pending_count(self) -> int:
        return len(self.items) - self.completed_count

    def review_for(self, item_id: str) -> TemporalReviewItem:
        item = next(
            (item for item in self.items if item.item_id == item_id), None)
        if item is None:
            raise KeyError(f"Unknown Temporal review item: {item_id}")
        return item

    def set_cursor(self, index: int) -> TemporalReviewItem | None:
        if not self.items:
            self._current_index = 0
            return None
        self._current_index = max(0, min(int(index), len(self.items) - 1))
        self.save()
        return self.current

    def navigate(
        self,
        direction: int,
        *,
        pending_only: bool = False,
    ) -> TemporalReviewItem | None:
        if not self.items:
            return None
        step = -1 if direction < 0 else 1
        if not pending_only:
            return self.set_cursor(self._current_index + step)
        for offset in range(1, len(self.items) + 1):
            index = (self._current_index + step * offset) % len(self.items)
            if not self.item_complete(self.items[index]):
                return self.set_cursor(index)
        return self.current

    def seek_target_ms(self, item_id: str, angle: int) -> int:
        return self.review_for(item_id).angle(angle).proposed_onset_ms

    def preview_range_ms(
        self,
        item_id: str,
        angle: int,
        *,
        lead_ms: int = 2_000,
        tail_ms: int = 3_000,
    ) -> tuple[int, int]:
        if lead_ms < 0 or tail_ms < 0:
            raise ValueError("Preview lead and tail must be non-negative")
        item = self.review_for(item_id)
        measured = item.angle(angle)
        angle_start_ms = item.start_ms + measured.range_start_clip_ms
        angle_end_ms = item.start_ms + measured.range_end_clip_ms
        preview_start = max(
            item.start_ms,
            angle_start_ms,
            measured.proposed_onset_ms - int(lead_ms),
        )
        preview_end = min(
            item.end_ms,
            angle_end_ms,
            measured.proposed_onset_ms + int(tail_ms),
        )
        if preview_end < preview_start:
            preview_end = preview_start
        return preview_start, preview_end

    def judge_angle(
        self,
        item_id: str,
        angle: int,
        judgment: str,
    ) -> TemporalReviewItem:
        normalized = str(judgment).strip().casefold()
        if normalized not in ANGLE_JUDGMENTS:
            raise ValueError(f"Unsupported angle judgment: {judgment!r}")
        item = self.review_for(item_id)
        measured = item.angle(angle)
        measured.judgment = normalized
        measured.reviewed_at = _utc_now()
        item.completed_at = _utc_now() if item.complete else ""
        self.save()
        return item

    def clear_angle_judgment(
        self,
        item_id: str,
        angle: int,
    ) -> TemporalReviewItem:
        item = self.review_for(item_id)
        measured = item.angle(angle)
        measured.judgment = ""
        measured.reviewed_at = ""
        item.completed_at = ""
        self.save()
        return item

    def mark_actual_snap(
        self,
        item_id: str,
        angle: int,
        actual_snap_ms: int,
    ) -> TemporalReviewItem:
        """Store an exact source-film snap inside the selected angle only."""

        try:
            source_ms = int(actual_snap_ms)
        except (TypeError, ValueError) as exc:
            raise ValueError("Actual snap must be an integer source time") \
                from exc
        item = self.review_for(item_id)
        measured = item.angle(angle)
        range_start_ms = item.start_ms + measured.range_start_clip_ms
        range_end_ms = item.start_ms + measured.range_end_clip_ms
        if not range_start_ms <= source_ms <= range_end_ms:
            raise ValueError(
                f"Actual snap {source_ms}ms lies outside angle {angle} "
                f"source range {range_start_ms}..{range_end_ms}ms"
            )
        measured.actual_snap_ms = source_ms
        measured.snap_status = "marked"
        measured.snap_reviewed_at = _utc_now()
        self.save()
        return item

    def mark_snap_unavailable(
        self,
        item_id: str,
        angle: int,
        status: str,
    ) -> TemporalReviewItem:
        """Record why no defensible exact snap can be marked."""

        normalized = str(status).strip().casefold()
        if normalized not in {"not_visible", "unsure"}:
            raise ValueError(
                "Unavailable snap status must be 'not_visible' or 'unsure'")
        item = self.review_for(item_id)
        measured = item.angle(angle)
        measured.actual_snap_ms = None
        measured.snap_status = normalized
        measured.snap_reviewed_at = _utc_now()
        self.save()
        return item

    def clear_snap_calibration(
        self,
        item_id: str,
        angle: int,
    ) -> TemporalReviewItem:
        """Return one angle to the pending calibration state."""

        item = self.review_for(item_id)
        measured = item.angle(angle)
        measured.actual_snap_ms = None
        measured.snap_status = ""
        measured.snap_reviewed_at = ""
        self.save()
        return item

    # Short alias for callers that present the action simply as "Clear".
    def clear_snap(
        self,
        item_id: str,
        angle: int,
    ) -> TemporalReviewItem:
        return self.clear_snap_calibration(item_id, angle)

    def judge_split(
        self,
        item_id: str,
        judgment: str,
    ) -> TemporalReviewItem:
        normalized = str(judgment).strip().casefold()
        if normalized not in SPLIT_JUDGMENTS:
            raise ValueError(f"Unsupported split judgment: {judgment!r}")
        item = self.review_for(item_id)
        item.split.judgment = normalized
        item.split.reviewed_at = _utc_now()
        self.save()
        return item

    def bind_project(
        self,
        current_project_path: Path | str,
        clips: Iterable[object],
        *,
        current_project_path_candidates: Sequence[Path | str] = (),
    ) -> list[TemporalReviewItem]:
        """Validate queued items against an already-loaded project snapshot.

        ``current_project_path_candidates`` lets a caller supply known aliases
        (for example a redirected Documents path).  Only file-name identity is
        used, then stable clip id and exact start/end bounds provide the real
        safety check.  No database connection is created here.
        """

        paths = [
            Path(current_project_path),
            *(Path(value) for value in current_project_path_candidates),
        ]
        candidate_names = {
            path.name.casefold() for path in paths if path.name}
        matched = [
            item for item in self.items
            if item.project_file_name.casefold() in candidate_names
        ]
        if not matched:
            expected = ", ".join(sorted({
                item.project_file_name for item in self.items
            }))
            raise ValueError(
                f"No Temporal review items match project file "
                f"{Path(current_project_path).name!r}; expected {expected}")

        snapshots: dict[str, tuple[int, int]] = {}
        for clip in clips:
            if isinstance(clip, Mapping):
                clip_id = str(clip.get("id", ""))
                start_ms = int(clip.get("start_ms", -1))
                end_ms = int(clip.get("end_ms", -1))
            else:
                clip_id = str(getattr(clip, "id", ""))
                start_ms = int(getattr(clip, "start_ms", -1))
                end_ms = int(getattr(clip, "end_ms", -1))
            if clip_id:
                snapshots[clip_id] = (start_ms, end_ms)

        for item in matched:
            actual = snapshots.get(item.clip_id)
            expected = (item.start_ms, item.end_ms)
            if actual is None:
                raise ValueError(
                    f"Temporal review clip {item.clip_id!r} is missing from "
                    "the current project")
            if actual != expected:
                raise ValueError(
                    f"Temporal review clip {item.clip_id!r} boundaries "
                    f"changed: expected {expected}, found {actual}")
        return matched

    def save(self) -> None:
        self.updated_at = _utc_now()
        item_payloads: list[dict[str, Any]] = []
        for item in self.items:
            item_payload = item.to_dict()
            item_payload["status"] = (
                "complete" if self.item_complete(item) else "pending")
            item_payloads.append(item_payload)
        payload = {
            "schema_version": STATE_SCHEMA_VERSION,
            "dataset_kind": STATE_DATASET_KIND,
            "review_id": self.review_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "only_flagged": self.only_flagged,
            "workflow": self.workflow,
            "judgments_path": str(self.judgments_path),
            "current_index": self._current_index,
            "cursor_item_id": self.current.item_id if self.current else "",
            "source_manifests": [
                manifest.to_dict() for manifest in self.manifests
            ],
            "items": item_payloads,
        }
        _atomic_write(
            self.state_path,
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ) + "\n",
        )
        self._write_completed_judgments()

    def _write_completed_judgments(self) -> None:
        if self.workflow == "snap_calibration":
            self._write_completed_calibrations()
            return

        records: list[dict[str, Any]] = []
        manifest_hashes = [
            manifest.to_dict() for manifest in self.manifests
        ]
        for item in self.items:
            if not item.complete:
                continue
            records.append({
                "schema_version": STATE_SCHEMA_VERSION,
                "dataset_kind": JUDGMENT_DATASET_KIND,
                "review_id": self.review_id,
                "source_manifests": manifest_hashes,
                "item_id": item.item_id,
                "research_cohort_id": item.research_cohort_id,
                "project_name": item.project_name,
                "project_file_name": item.project_file_name,
                "source_video_path": item.source_video_path,
                "clip_id": item.clip_id,
                "clip_number": item.clip_number,
                "start_ms": item.start_ms,
                "end_ms": item.end_ms,
                "measurement_signature": item.measurement_signature,
                "split": item.split.to_dict(),
                "angles": [angle.to_dict() for angle in item.angles],
                "completed_at": item.completed_at,
            })
        text = "".join(
            json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
            ) + "\n"
            for record in records
        )
        _atomic_write(self.judgments_path, text)

    def _write_completed_calibrations(self) -> None:
        """Write only completed, label-blind exact-snap measurements."""

        records: list[dict[str, Any]] = []
        manifest_hashes = [
            manifest.to_dict() for manifest in self.manifests
        ]
        for item in self.items:
            if not item.snap_complete:
                continue
            reviewed_times = [
                angle.snap_reviewed_at
                for angle in item.angles
                if angle.snap_reviewed_at
            ]
            records.append({
                "schema_version": STATE_SCHEMA_VERSION,
                "dataset_kind": CALIBRATION_DATASET_KIND,
                "workflow": "snap_calibration",
                "review_id": self.review_id,
                "source_manifests": manifest_hashes,
                "item_id": item.item_id,
                "research_cohort_id": item.research_cohort_id,
                "project_name": item.project_name,
                "project_file_name": item.project_file_name,
                "source_video_path": item.source_video_path,
                "clip_id": item.clip_id,
                "clip_number": item.clip_number,
                "start_ms": item.start_ms,
                "end_ms": item.end_ms,
                "measurement_signature": item.measurement_signature,
                "angles": [
                    {
                        "angle": angle.angle,
                        "range_start_ms": (
                            item.start_ms + angle.range_start_clip_ms
                        ),
                        "range_end_ms": (
                            item.start_ms + angle.range_end_clip_ms
                        ),
                        "proposed_onset_ms": angle.proposed_onset_ms,
                        "actual_snap_ms": angle.actual_snap_ms,
                        "snap_status": angle.snap_status,
                        "snap_reviewed_at": angle.snap_reviewed_at,
                        "delta_ms": angle.snap_delta_ms,
                    }
                    for angle in item.angles
                ],
                "completed_at": (
                    max(reviewed_times) if reviewed_times else ""
                ),
            })
        text = "".join(
            json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
            ) + "\n"
            for record in records
        )
        _atomic_write(self.judgments_path, text)
