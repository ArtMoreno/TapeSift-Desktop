"""Human verification state for the offline segmentation benchmark.

The verifier writes only research JSON and JSONL files. It never opens or
updates a TapeSift project database.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tapesift.research.segmentation_benchmark import (
    SCHEMA_VERSION, artifact_path, load_manifest,
)
from tapesift.services.ffprobe_service import probe_video
from tapesift.services.play_detect_service import SCENE_PAIR_REVIEW_REASON

MIN_SEGMENT_MS = 500
MAX_MERGE_GAP_MS = 5_000
MAX_BLIND_REVIEW_WINDOW_MS = 90_000
PILOT_SCHEMA_VERSION = "1.0"
PAIR_SELECTION_SCHEMA_VERSION = "1.0"
VERIFICATION_STRATA = (
    "confident",
    "weak_recovered",
    "other_review",
    "unclassified",
    "scene_angle_pair",
)
VERIFICATION_STRATUM_ALIASES = {
    "recovered": "weak_recovered",
    "review": "other_review",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


@dataclass
class VerificationItem:
    item_id: str
    film_id: str
    film_name: str
    source_file: str
    analysis_source: str
    frame_rate: float
    film_duration_ms: int
    start_ms: int
    end_ms: int
    original_start_ms: int
    original_end_ms: int
    candidate_kind: str
    candidate_index: int
    prediction_indices: list[int] = field(default_factory=list)
    angle_starts_ms: list[int] = field(default_factory=list)
    detector_needs_review: bool = False
    detector_reason: str = ""
    detector_signal: str = ""
    angle_count: int = 1
    # Sampling bucket used by named benchmark queues. Older verifier state
    # files omit this field and load with an empty value.
    candidate_stratum: str = ""
    status: str = "pending"
    decision: str = ""
    edit_history: list[str] = field(default_factory=list)
    verified_at: str = ""

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "VerificationItem":
        known = {entry.name for entry in fields(cls)}
        return cls(**{key: value for key, value in payload.items()
                      if key in known})


@dataclass
class _Snapshot:
    items: list[VerificationItem]
    cursor: int
    action: str


def _normalized_angle_metadata(
    start_ms: int,
    end_ms: int,
    angle_starts_ms: list[int],
) -> tuple[list[int], int]:
    """Keep angle markers valid after a reviewer changes segment bounds."""

    starts = sorted({
        int(value)
        for value in angle_starts_ms
        if start_ms <= int(value) < end_ms
    })
    if not starts or starts[0] != start_ms:
        starts.insert(0, start_ms)
    return starts, len(starts)


class VerificationSession:
    """Mutable verification queue with immediate JSON and JSONL autosave."""

    def __init__(
        self,
        state_path: Path,
        ground_truth_path: Path,
        queue_id: str,
        items: list[VerificationItem],
        *,
        cursor: int = 0,
        created_at: str = "",
    ) -> None:
        self.state_path = state_path
        self.ground_truth_path = ground_truth_path
        self.queue_id = queue_id
        self.items = items
        self.cursor = max(0, min(cursor, max(0, len(items) - 1)))
        self.created_at = created_at or utc_now()
        self.updated_at = self.created_at
        self.last_action = ""
        self._undo_stack: list[_Snapshot] = []

    @classmethod
    def create(
        cls,
        state_path: Path,
        ground_truth_path: Path,
        queue_id: str,
        items: list[VerificationItem],
    ) -> "VerificationSession":
        session = cls(state_path, ground_truth_path, queue_id, items)
        session.save()
        return session

    @classmethod
    def load(cls, state_path: Path) -> "VerificationSession":
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != PILOT_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported verifier state schema: "
                f"{payload.get('schema_version')!r}")
        items = [
            VerificationItem.from_dict(item)
            for item in payload.get("items", [])
        ]
        cursor_id = payload.get("cursor_item_id", "")
        cursor = next(
            (index for index, item in enumerate(items)
             if item.item_id == cursor_id),
            int(payload.get("cursor", 0)),
        )
        session = cls(
            state_path,
            Path(payload["ground_truth_path"]),
            payload["queue_id"],
            items,
            cursor=cursor,
            created_at=payload.get("created_at", ""),
        )
        session.updated_at = payload.get("updated_at", session.created_at)
        session.last_action = payload.get("last_action", "")
        return session

    @property
    def current(self) -> VerificationItem | None:
        return self.items[self.cursor] if self.items else None

    @property
    def completed_count(self) -> int:
        return sum(item.status in {"verified", "excluded"}
                   for item in self.items)

    @property
    def pending_count(self) -> int:
        return sum(item.status == "pending" for item in self.items)

    @property
    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    def save(self) -> None:
        self.updated_at = utc_now()
        payload = {
            "schema_version": PILOT_SCHEMA_VERSION,
            "queue_id": self.queue_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "ground_truth_path": str(self.ground_truth_path),
            "cursor": self.cursor,
            "cursor_item_id": self.current.item_id if self.current else "",
            "last_action": self.last_action,
            "items": [asdict(item) for item in self.items],
        }
        _write_atomic(
            self.state_path,
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
        )
        self._write_ground_truth()

    def _write_ground_truth(self) -> None:
        records = []
        for item in self.items:
            if item.status not in {"verified", "excluded"}:
                continue
            records.append({
                "schema_version": SCHEMA_VERSION,
                "queue_id": self.queue_id,
                "item_id": item.item_id,
                "film_id": item.film_id,
                "source_file": item.source_file,
                "start_ms": item.start_ms,
                "end_ms": item.end_ms,
                "angle_starts_ms": item.angle_starts_ms,
                "verification_status": item.status,
                "decision": item.decision,
                "edit_history": item.edit_history,
                "verified_at": item.verified_at,
                "detector": {
                    "candidate_kind": item.candidate_kind,
                    "candidate_stratum": item.candidate_stratum,
                    "candidate_index": item.candidate_index,
                    "prediction_indices": item.prediction_indices,
                    "original_start_ms": item.original_start_ms,
                    "original_end_ms": item.original_end_ms,
                    "needs_review": item.detector_needs_review,
                    "reason": item.detector_reason,
                    "signal": item.detector_signal,
                    "angle_count": item.angle_count,
                },
            })
        text = "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in records
        )
        _write_atomic(self.ground_truth_path, text)

    def set_cursor(self, index: int) -> None:
        if not self.items:
            self.cursor = 0
            return
        self.cursor = max(0, min(index, len(self.items) - 1))
        self.last_action = "navigate"
        self.save()

    def navigate(self, direction: int) -> None:
        self.set_cursor(self.cursor + direction)

    def _snapshot(self, action: str) -> None:
        self._undo_stack.append(
            _Snapshot(copy.deepcopy(self.items), self.cursor, action))
        if len(self._undo_stack) > 100:
            del self._undo_stack[0]

    def _next_pending(self, after_index: int) -> int:
        for index in range(after_index + 1, len(self.items)):
            if self.items[index].status == "pending":
                return index
        for index in range(0, after_index):
            if self.items[index].status == "pending":
                return index
        return max(0, min(after_index, len(self.items) - 1))

    def accept(self) -> None:
        item = self.current
        if item is None:
            return
        self._snapshot("accept")
        item.status = "verified"
        item.decision = "accepted" if not item.edit_history else "revised"
        item.verified_at = utc_now()
        self.cursor = self._next_pending(self.cursor)
        self.last_action = f"accepted {item.item_id}"
        self.save()

    def exclude(self) -> None:
        item = self.current
        if item is None:
            return
        self._snapshot("exclude")
        item.status = "excluded"
        item.decision = "excluded"
        item.verified_at = utc_now()
        self.cursor = self._next_pending(self.cursor)
        self.last_action = f"excluded {item.item_id}"
        self.save()

    def split(self, point_ms: int) -> None:
        item = self.current
        if item is None:
            return
        if not item.start_ms + MIN_SEGMENT_MS <= point_ms \
                <= item.end_ms - MIN_SEGMENT_MS:
            raise ValueError("Split point must be inside the current segment")
        self._snapshot("split")
        token = uuid.uuid4().hex[:8]
        shared = asdict(item)
        left_starts, left_count = _normalized_angle_metadata(
            item.start_ms, point_ms, item.angle_starts_ms)
        right_starts, right_count = _normalized_angle_metadata(
            point_ms, item.end_ms, item.angle_starts_ms)
        left = VerificationItem.from_dict({
            **shared,
            "item_id": f"{item.item_id}:split:{token}:a",
            "end_ms": point_ms,
            "angle_starts_ms": left_starts,
            "angle_count": left_count,
            "status": "pending",
            "decision": "",
            "verified_at": "",
            "edit_history": [*item.edit_history, f"split@{point_ms}:left"],
        })
        right = VerificationItem.from_dict({
            **shared,
            "item_id": f"{item.item_id}:split:{token}:b",
            "start_ms": point_ms,
            "angle_starts_ms": right_starts,
            "angle_count": right_count,
            "status": "pending",
            "decision": "",
            "verified_at": "",
            "edit_history": [*item.edit_history, f"split@{point_ms}:right"],
        })
        self.items[self.cursor:self.cursor + 1] = [left, right]
        self.last_action = f"split {item.item_id} at {point_ms}"
        self.save()

    def merge_error(self, direction: int) -> str | None:
        """Return why a merge is unavailable, or None when it is valid."""
        item = self.current
        if item is None:
            return "There is no selected segment to merge"
        if direction not in {-1, 1}:
            return "Merge direction must be previous or next"
        other_index = self.cursor + direction
        if not 0 <= other_index < len(self.items):
            return "There is no adjacent segment to merge"
        other = self.items[other_index]
        if other.film_id != item.film_id:
            return "Segments from different films cannot be merged"
        gap_ms = max(
            0,
            max(item.start_ms, other.start_ms)
            - min(item.end_ms, other.end_ms),
        )
        if gap_ms > MAX_MERGE_GAP_MS:
            return (
                "Only neighboring predictions can be merged. "
                "The next sampled prediction is too far away."
            )
        return None

    def can_merge(self, direction: int) -> bool:
        return self.merge_error(direction) is None

    def merge(self, direction: int) -> None:
        error = self.merge_error(direction)
        if error is not None:
            raise ValueError(error)
        item = self.current
        assert item is not None
        other_index = self.cursor + direction
        other = self.items[other_index]
        self._snapshot("merge")
        first_index = min(self.cursor, other_index)
        second_index = max(self.cursor, other_index)
        first = self.items[first_index]
        second = self.items[second_index]
        merged_start = min(first.start_ms, second.start_ms)
        merged_end = max(first.end_ms, second.end_ms)
        merged_starts, merged_count = _normalized_angle_metadata(
            merged_start,
            merged_end,
            [
                first.start_ms,
                *first.angle_starts_ms,
                second.start_ms,
                *second.angle_starts_ms,
            ],
        )
        merged = VerificationItem.from_dict({
            **asdict(first),
            "item_id": (
                f"{first.item_id}:merge:{uuid.uuid4().hex[:8]}"),
            "start_ms": merged_start,
            "end_ms": merged_end,
            "original_start_ms": min(
                first.original_start_ms, second.original_start_ms),
            "original_end_ms": max(
                first.original_end_ms, second.original_end_ms),
            "prediction_indices": sorted(set(
                first.prediction_indices + second.prediction_indices)),
            "angle_starts_ms": merged_starts,
            "detector_needs_review": (
                first.detector_needs_review
                or second.detector_needs_review),
            "detector_reason": " | ".join(
                value for value in (
                    first.detector_reason, second.detector_reason)
                if value),
            "angle_count": merged_count,
            "status": "pending",
            "decision": "",
            "verified_at": "",
            "edit_history": [
                *first.edit_history,
                *second.edit_history,
                f"merged:{first.item_id}+{second.item_id}",
            ],
        })
        self.items[first_index:second_index + 1] = [merged]
        self.cursor = first_index
        self.last_action = f"merged {first.item_id} and {second.item_id}"
        self.save()

    def set_start(self, point_ms: int) -> None:
        item = self.current
        if item is None:
            return
        point_ms = max(0, point_ms)
        if point_ms > item.end_ms - MIN_SEGMENT_MS:
            raise ValueError("Start must remain before the segment end")
        self._snapshot("set start")
        item.start_ms = point_ms
        item.angle_starts_ms, item.angle_count = _normalized_angle_metadata(
            item.start_ms, item.end_ms, item.angle_starts_ms)
        item.status = "pending"
        item.decision = ""
        item.verified_at = ""
        item.edit_history.append(f"start@{point_ms}")
        self.last_action = f"set start of {item.item_id}"
        self.save()

    def set_end(self, point_ms: int) -> None:
        item = self.current
        if item is None:
            return
        point_ms = min(item.film_duration_ms, point_ms) \
            if item.film_duration_ms else point_ms
        if point_ms < item.start_ms + MIN_SEGMENT_MS:
            raise ValueError("End must remain after the segment start")
        self._snapshot("set end")
        item.end_ms = point_ms
        item.angle_starts_ms, item.angle_count = _normalized_angle_metadata(
            item.start_ms, item.end_ms, item.angle_starts_ms)
        item.status = "pending"
        item.decision = ""
        item.verified_at = ""
        item.edit_history.append(f"end@{point_ms}")
        self.last_action = f"set end of {item.item_id}"
        self.save()

    def undo(self) -> str:
        if not self._undo_stack:
            return ""
        snapshot = self._undo_stack.pop()
        self.items = snapshot.items
        self.cursor = snapshot.cursor
        self.last_action = f"undid {snapshot.action}"
        self.save()
        return snapshot.action


def _even_sample(items: list[dict[str, Any]], count: int
                 ) -> list[dict[str, Any]]:
    if count <= 0 or not items:
        return []
    if count >= len(items):
        return list(items)
    if count == 1:
        return [items[len(items) // 2]]
    indexes = {
        round(index * (len(items) - 1) / (count - 1))
        for index in range(count)
    }
    return [items[index] for index in sorted(indexes)]


def _file_sha256(path: Path | None) -> str:
    if path is None or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_pair_proposal_items(
    manifest_path: Path,
    film_id: str,
    prediction_path: Path,
    ffprobe_path: Path,
    *,
    count: int,
    review_reason: str,
    baseline_prediction_path: Path | None = None,
    reference_truth_path: Path | None = None,
    detector_source_path: Path | None = None,
    candidate_kind: str = "scene_angle_pair",
) -> tuple[list[VerificationItem], dict[str, Any]]:
    """Build a blind queue from one exact guarded two-angle proposal type."""
    if count <= 0:
        raise ValueError("Pair proposal count must be positive")
    if candidate_kind not in {"scene_angle_pair", "black_gap_pair"}:
        raise ValueError(f"Unsupported pair proposal kind: {candidate_kind}")
    manifest = load_manifest(manifest_path)
    films = {film["film_id"]: film for film in manifest["films"]}
    if film_id not in films:
        raise ValueError(f"Unknown film id: {film_id}")
    film = films[film_id]
    prediction_path = prediction_path.resolve()
    if not prediction_path.is_file():
        raise FileNotFoundError(prediction_path)
    prediction = json.loads(prediction_path.read_text(encoding="utf-8"))
    if prediction.get("film_id") != film_id:
        raise ValueError(
            f"Pair prediction belongs to {prediction.get('film_id')!r}, "
            f"not {film_id!r}")

    duration_ms = int(prediction.get("duration_ms") or 0)
    max_play_ms = int(
        prediction.get("summary", {}).get("profile", {}).get(
            "max_play_ms") or 0)
    if duration_ms <= 0 or max_play_ms <= 0:
        raise ValueError("Pair prediction is missing duration or play ceiling")

    plays = prediction.get("plays", [])
    previous_end = 0
    proposals: list[dict[str, Any]] = []
    for output_index, play in enumerate(plays):
        start_ms = int(play.get("start_ms") or 0)
        end_ms = int(play.get("end_ms") or 0)
        if not 0 <= start_ms < end_ms <= duration_ms:
            raise ValueError(
                f"Prediction {output_index} is outside the film bounds")
        if output_index and start_ms < previous_end:
            raise ValueError("Pair prediction contains overlapping plays")
        previous_end = end_ms

        angle_starts = [
            int(value) for value in play.get("angle_starts", [])]
        eligible = (
            bool(play.get("needs_review"))
            and play.get("review_reason", "") == review_reason
            and int(play.get("angle_count") or 0) == 2
            and len(angle_starts) == 2
            and angle_starts == sorted(angle_starts)
            and start_ms == angle_starts[0]
            and start_ms < angle_starts[1] < end_ms
            and end_ms - start_ms <= max_play_ms
        )
        if not eligible:
            continue
        proposals.append({
            "output_index": output_index,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "angle_starts_ms": angle_starts,
        })
    proposals.sort(
        key=lambda item: (
            item["start_ms"], item["end_ms"], item["output_index"]))
    if len(proposals) < count:
        raise ValueError(
            f"Only {len(proposals)} valid pair proposals exist; "
            f"{count} are required")
    selected = _even_sample(proposals, count)
    if len(selected) != count:
        raise RuntimeError("Systematic pair sampling did not reach its target")

    baseline_components: dict[int, list[int]] = {}
    if baseline_prediction_path is not None \
            and baseline_prediction_path.is_file():
        baseline = json.loads(
            baseline_prediction_path.read_text(encoding="utf-8"))
        starts: dict[int, list[int]] = {}
        for index, play in enumerate(baseline.get("plays", [])):
            starts.setdefault(int(play["start_ms"]), []).append(index)
        for proposal in selected:
            indexes: list[int] = []
            for angle_start in proposal["angle_starts_ms"]:
                matches = starts.get(angle_start, [])
                if len(matches) != 1:
                    indexes = []
                    break
                indexes.append(matches[0])
            baseline_components[proposal["output_index"]] = indexes

    analysis_source = Path(
        film.get("analysis_source") or film["source_file"])
    metadata = probe_video(str(ffprobe_path), analysis_source)
    items = [
        VerificationItem(
            item_id=(
                f"{film_id}:{candidate_kind}:"
                f"{proposal['output_index']:04d}"),
            film_id=film_id,
            film_name=Path(film["source_file"]).stem,
            source_file=film["source_file"],
            analysis_source=str(analysis_source),
            frame_rate=metadata.frame_rate or 30.0,
            film_duration_ms=metadata.duration_ms or duration_ms,
            start_ms=proposal["start_ms"],
            end_ms=proposal["end_ms"],
            original_start_ms=proposal["start_ms"],
            original_end_ms=proposal["end_ms"],
            candidate_kind=candidate_kind,
            candidate_index=proposal["output_index"],
            prediction_indices=[proposal["output_index"]],
            angle_starts_ms=list(proposal["angle_starts_ms"]),
            detector_needs_review=True,
            detector_reason=review_reason,
            detector_signal=prediction.get(
                "summary", {}).get("signal", ""),
            angle_count=2,
            candidate_stratum="paired",
        )
        for proposal in selected
    ]
    sidecar = {
        "schema_version": PAIR_SELECTION_SCHEMA_VERSION,
        "created_at": utc_now(),
        "film_id": film_id,
        "prediction_file": str(prediction_path),
        "prediction_sha256": _file_sha256(prediction_path),
        "baseline_prediction_file": (
            str(baseline_prediction_path.resolve())
            if baseline_prediction_path is not None else ""),
        "baseline_prediction_sha256": _file_sha256(
            baseline_prediction_path),
        "reference_truth_file": (
            str(reference_truth_path.resolve())
            if reference_truth_path is not None else ""),
        "reference_truth_sha256": _file_sha256(reference_truth_path),
        "detector_source_file": (
            str(detector_source_path.resolve())
            if detector_source_path is not None else ""),
        "detector_source_sha256": _file_sha256(detector_source_path),
        "selection_strategy": (
            "systematic even sample over time from exact guarded "
            "two-angle pair proposals; no truth consulted"),
        "candidate_kind": candidate_kind,
        "proposal_pool_count": len(proposals),
        "selected_count": len(selected),
        "review_reason": review_reason,
        "selected": [
            {
                **proposal,
                "baseline_component_indices": baseline_components.get(
                    proposal["output_index"], []),
            }
            for proposal in selected
        ],
    }
    return items, sidecar


def _select_candidates(candidates: list[dict[str, Any]],
                       count: int) -> list[dict[str, Any]]:
    unclassified = [
        item for item in candidates if item["candidate_kind"] == "unclassified"
    ]
    review = [
        item for item in candidates
        if item["candidate_kind"] == "play"
        and item["detector_needs_review"]
    ]
    confident = [
        item for item in candidates
        if item["candidate_kind"] == "play"
        and not item["detector_needs_review"]
    ]
    selected = _even_sample(unclassified, min(len(unclassified), count))
    remaining = count - len(selected)
    review_target = min(len(review), math.ceil(remaining * 0.70))
    selected.extend(_even_sample(review, review_target))
    remaining = count - len(selected)
    selected.extend(_even_sample(confident, remaining))
    remaining = count - len(selected)
    if remaining:
        already = {id(item) for item in selected}
        leftovers = [item for item in candidates if id(item) not in already]
        selected.extend(_even_sample(leftovers, remaining))
    return sorted(selected[:count], key=lambda item: item["start_ms"])


def _select_stratified_candidates(
    candidates: list[dict[str, Any]],
    count: int,
    strata: dict[str, int],
) -> list[dict[str, Any]]:
    """Select explicit benchmark strata, then backfill unavailable buckets."""
    valid = set(VERIFICATION_STRATA) | set(VERIFICATION_STRATUM_ALIASES)
    unknown = set(strata) - valid
    if unknown:
        raise ValueError(
            f"Unknown verification strata: {', '.join(sorted(unknown))}")
    if any(value < 0 for value in strata.values()):
        raise ValueError("Verification stratum counts cannot be negative")
    normalized: dict[str, int] = {}
    for name, value in strata.items():
        canonical = VERIFICATION_STRATUM_ALIASES.get(name, name)
        normalized[canonical] = normalized.get(canonical, 0) + value
    if sum(normalized.values()) != count:
        raise ValueError(
            f"Verification strata total {sum(normalized.values())}, "
            f"but the film selection requests {count}")

    selected: list[dict[str, Any]] = []
    for stratum in VERIFICATION_STRATA:
        bucket = [
            item for item in candidates
            if item["candidate_stratum"] == stratum
        ]
        selected.extend(
            _even_time_sample(bucket, normalized.get(stratum, 0)))

    remaining = count - len(selected)
    if remaining:
        selected_ids = {id(item) for item in selected}
        leftovers = sorted(
            (
                item for item in candidates
                if id(item) not in selected_ids
            ),
            key=lambda item: (
                item["start_ms"],
                item.get("end_ms", item["start_ms"]),
                item.get("candidate_kind", ""),
            ),
        )
        selected.extend(_even_time_sample(leftovers, remaining))
    return sorted(selected[:count], key=lambda item: item["start_ms"])


def _even_time_sample(
    candidates: list[dict[str, Any]],
    count: int,
) -> list[dict[str, Any]]:
    """Select candidates nearest evenly spaced time targets."""
    ordered = sorted(
        candidates,
        key=lambda item: (
            item["start_ms"],
            item.get("end_ms", item["start_ms"]),
            item.get("candidate_kind", ""),
        ),
    )
    if count <= 0:
        return []
    if count >= len(ordered):
        return ordered

    range_start = min(item["start_ms"] for item in ordered)
    range_end = max(item.get("end_ms", item["start_ms"]) for item in ordered)
    span = max(1, range_end - range_start)
    available = list(enumerate(ordered))
    selected: list[dict[str, Any]] = []
    for index in range(count):
        target = range_start + span * (2 * index + 1) / (2 * count)
        chosen = min(
            available,
            key=lambda entry: (
                abs(
                    (
                        entry[1]["start_ms"]
                        + entry[1].get("end_ms", entry[1]["start_ms"])
                    ) / 2 - target
                ),
                entry[1]["start_ms"],
                entry[0],
            ),
        )
        selected.append(chosen[1])
        available.remove(chosen)
    return sorted(
        selected,
        key=lambda item: (
            item["start_ms"],
            item.get("end_ms", item["start_ms"]),
            item.get("candidate_kind", ""),
        ),
    )


def _blind_unclassified_windows(
    segment: dict[str, Any],
    *,
    max_window_ms: int = MAX_BLIND_REVIEW_WINDOW_MS,
) -> list[tuple[int, int]]:
    """Turn detector-emitted split points into bounded blind review windows."""
    start_ms = int(segment["start_ms"])
    end_ms = int(segment["end_ms"])
    split_points = sorted({
        int(value)
        for value in segment.get("split_points_ms", [])
        if start_ms < int(value) < end_ms
    })
    boundaries = [start_ms, *split_points, end_ms]
    windows: list[tuple[int, int]] = []
    for left, right in zip(boundaries, boundaries[1:]):
        duration_ms = right - left
        part_count = max(1, math.ceil(duration_ms / max_window_ms))
        for part in range(part_count):
            part_start = left + (duration_ms * part) // part_count
            part_end = left + (duration_ms * (part + 1)) // part_count
            if part_end > part_start:
                windows.append((part_start, part_end))
    return windows


def build_pilot_items(
    manifest_path: Path,
    selections: list[tuple[str, int]],
    ffprobe_path: Path,
    *,
    strata: dict[str, int] | None = None,
) -> list[VerificationItem]:
    """Create a verification queue from completed detector predictions."""
    manifest = load_manifest(manifest_path)
    films = {film["film_id"]: film for film in manifest["films"]}
    items: list[VerificationItem] = []
    for film_id, count in selections:
        if film_id not in films:
            raise ValueError(f"Unknown film id: {film_id}")
        film = films[film_id]
        prediction_path = artifact_path(
            manifest_path, film["prediction_file"])
        prediction = json.loads(
            prediction_path.read_text(encoding="utf-8"))
        analysis_source = Path(
            film.get("analysis_source") or film["source_file"])
        metadata = probe_video(str(ffprobe_path), analysis_source)
        candidates: list[dict[str, Any]] = []
        for index, play in enumerate(prediction["plays"]):
            needs_review = bool(play.get("needs_review"))
            review_reason = play.get("review_reason", "")
            if review_reason == SCENE_PAIR_REVIEW_REASON:
                candidate_kind = "scene_angle_pair"
                candidate_stratum = "scene_angle_pair"
            elif not needs_review:
                candidate_kind = "play"
                candidate_stratum = "confident"
            elif "weak scene" in review_reason.casefold():
                candidate_kind = "play"
                candidate_stratum = "weak_recovered"
            else:
                candidate_kind = "play"
                candidate_stratum = "other_review"
            candidates.append({
                "candidate_kind": candidate_kind,
                "candidate_stratum": candidate_stratum,
                "candidate_index": index,
                "start_ms": play["start_ms"],
                "end_ms": play["end_ms"],
                "angle_starts_ms": play.get("angle_starts", []),
                "detector_needs_review": needs_review,
                "detector_reason": review_reason,
                "angle_count": int(play.get("angle_count") or 1),
                "prediction_indices": [index],
            })
        unclassified_index = 0
        for segment in prediction.get("unclassified", []):
            for window_start, window_end in _blind_unclassified_windows(
                    segment):
                candidates.append({
                    "candidate_kind": "unclassified",
                    "candidate_stratum": "unclassified",
                    "candidate_index": unclassified_index,
                    "start_ms": window_start,
                    "end_ms": window_end,
                    "angle_starts_ms": [window_start],
                    "detector_needs_review": True,
                    "detector_reason": segment.get("reason", ""),
                    "angle_count": 1,
                    "prediction_indices": [],
                })
                unclassified_index += 1
        selected = (
            _select_stratified_candidates(candidates, count, strata)
            if strata is not None
            else _select_candidates(candidates, count)
        )
        for candidate in selected:
            item_id = (
                f"{film_id}:{candidate['candidate_kind']}:"
                f"{candidate['candidate_index']:04d}")
            items.append(VerificationItem(
                item_id=item_id,
                film_id=film_id,
                film_name=Path(film["source_file"]).stem,
                source_file=film["source_file"],
                analysis_source=str(analysis_source),
                frame_rate=metadata.frame_rate or 30.0,
                film_duration_ms=metadata.duration_ms,
                start_ms=candidate["start_ms"],
                end_ms=candidate["end_ms"],
                original_start_ms=candidate["start_ms"],
                original_end_ms=candidate["end_ms"],
                candidate_kind=candidate["candidate_kind"],
                candidate_index=candidate["candidate_index"],
                prediction_indices=candidate["prediction_indices"],
                angle_starts_ms=candidate["angle_starts_ms"],
                detector_needs_review=candidate["detector_needs_review"],
                detector_reason=candidate["detector_reason"],
                detector_signal=prediction["summary"]["signal"],
                angle_count=candidate["angle_count"],
                candidate_stratum=candidate["candidate_stratum"],
            ))
    return items
