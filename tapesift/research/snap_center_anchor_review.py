"""Deterministic spatial-anchor review package for snap-onset research.

The package deliberately labels position, not time. Exact snap timestamps are
read from the frozen development judgments and used only to extract a reference
frame 250 ms before the snap plus three context frames. The exported normalized
point can later constrain direct center/ball evidence without introducing
film-specific coordinates.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = "1.0"
ITERATION_ID = "iteration-7i-center-snap-anchor-review-v1"
REFERENCE_OFFSET_MS = -250
CONTEXT_OFFSETS_MS = (-500, -250, 0, 250)
VALID_STATUSES = frozenset({"visible", "occluded", "unsure"})


def _canonical_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} is not a JSON object")
            rows.append(value)
    return rows


def _safe_name(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    return result or "anchor"


def _failure_keys(payload: object) -> set[tuple[str, int]]:
    """Collect item/clip + angle keys from the frozen failure-atlas shape."""

    keys: set[tuple[str, int]] = set()

    def visit(value: object) -> None:
        if isinstance(value, list):
            for child in value:
                visit(child)
            return
        if not isinstance(value, dict):
            return
        try:
            angle = int(value.get("angle", value.get("angle_number", 0)))
        except (TypeError, ValueError):
            angle = 0
        if angle > 0:
            for field in ("item_id", "clip_id", "review_item_id"):
                identifier = str(value.get(field, "")).strip()
                if identifier:
                    keys.add((identifier, angle))
        for child in value.values():
            visit(child)

    visit(payload)
    return keys


@dataclass(frozen=True)
class AnchorReviewItem:
    review_item_id: str
    item_id: str
    research_cohort_id: str
    project_name: str
    clip_id: str
    clip_number: int
    angle: int
    source_video_path: str
    range_start_ms: int
    range_end_ms: int
    actual_snap_ms: int
    priority_failure: bool

    @property
    def reference_ms(self) -> int:
        return self.actual_snap_ms + REFERENCE_OFFSET_MS

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_item_id": self.review_item_id,
            "item_id": self.item_id,
            "research_cohort_id": self.research_cohort_id,
            "project_name": self.project_name,
            "clip_id": self.clip_id,
            "clip_number": self.clip_number,
            "angle": self.angle,
            "source_video_path": self.source_video_path,
            "range_start_ms": self.range_start_ms,
            "range_end_ms": self.range_end_ms,
            "actual_snap_ms": self.actual_snap_ms,
            "reference_offset_ms": REFERENCE_OFFSET_MS,
            "reference_ms": self.reference_ms,
            "priority_failure": self.priority_failure,
        }


def build_review_items(
    judgment_paths: Sequence[Path],
    failure_atlas_path: Path,
) -> list[AnchorReviewItem]:
    failure_payload = json.loads(failure_atlas_path.read_text(encoding="utf-8"))
    failures = _failure_keys(failure_payload)
    items: list[AnchorReviewItem] = []
    seen: set[str] = set()

    for judgment_path in judgment_paths:
        for row in _read_jsonl(judgment_path):
            item_id = str(row.get("item_id", "")).strip()
            clip_id = str(row.get("clip_id", "")).strip()
            source = str(row.get("source_video_path", "")).strip()
            angles = row.get("angles")
            if not item_id or not clip_id or not source or not isinstance(angles, list):
                raise ValueError("Snap judgment is missing item, clip, video, or angles")
            for angle_row in angles:
                if not isinstance(angle_row, dict):
                    continue
                if angle_row.get("snap_status") != "marked":
                    continue
                angle = int(angle_row["angle"])
                actual_snap_ms = int(angle_row["actual_snap_ms"])
                range_start_ms = int(angle_row["range_start_ms"])
                range_end_ms = int(angle_row["range_end_ms"])
                if not range_start_ms <= actual_snap_ms <= range_end_ms:
                    raise ValueError(f"{item_id} angle {angle} snap is outside its range")
                review_item_id = f"{item_id}:angle-{angle}"
                if review_item_id in seen:
                    raise ValueError(f"Duplicate anchor review item: {review_item_id}")
                seen.add(review_item_id)
                priority = (
                    (item_id, angle) in failures
                    or (clip_id, angle) in failures
                    or (review_item_id, angle) in failures
                )
                items.append(AnchorReviewItem(
                    review_item_id=review_item_id,
                    item_id=item_id,
                    research_cohort_id=str(row.get("research_cohort_id", "")),
                    project_name=str(row.get("project_name", "")),
                    clip_id=clip_id,
                    clip_number=int(row["clip_number"]),
                    angle=angle,
                    source_video_path=source,
                    range_start_ms=range_start_ms,
                    range_end_ms=range_end_ms,
                    actual_snap_ms=actual_snap_ms,
                    priority_failure=priority,
                ))

    return sorted(items, key=lambda item: (
        not item.priority_failure,
        item.research_cohort_id,
        item.clip_number,
        item.angle,
    ))


def validate_exported_label(
    label: Mapping[str, Any],
    item: AnchorReviewItem,
    reference_sha256: str,
) -> dict[str, Any]:
    status = str(label.get("anchor_status", ""))
    if status not in VALID_STATUSES:
        raise ValueError(f"Unsupported anchor status: {status!r}")
    if str(label.get("review_item_id", "")) != item.review_item_id:
        raise ValueError("Anchor label belongs to a different review item")
    if str(label.get("reference_sha256", "")) != reference_sha256:
        raise ValueError("Anchor label references a stale frame")
    x = label.get("anchor_x")
    y = label.get("anchor_y")
    if status == "visible":
        try:
            normalized_x = float(x)
            normalized_y = float(y)
        except (TypeError, ValueError) as exc:
            raise ValueError("Visible anchor requires normalized coordinates") from exc
        if not 0.0 <= normalized_x <= 1.0 or not 0.0 <= normalized_y <= 1.0:
            raise ValueError("Anchor coordinates must be between zero and one")
    elif x is not None or y is not None:
        raise ValueError("Unavailable anchor must not contain coordinates")
    return dict(label)


def _extract_frames(
    items: Iterable[AnchorReviewItem],
    output_dir: Path,
    *,
    max_width: int,
) -> list[dict[str, Any]]:
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - runtime dependency
        raise RuntimeError("OpenCV is required to prepare anchor frames") from exc

    frames_dir = output_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    captures: dict[str, Any] = {}
    records: list[dict[str, Any]] = []
    try:
        for item in items:
            capture = captures.get(item.source_video_path)
            if capture is None:
                capture = cv2.VideoCapture(item.source_video_path)
                if not capture.isOpened():
                    raise FileNotFoundError(item.source_video_path)
                captures[item.source_video_path] = capture
            assets: dict[str, Any] = {}
            for offset_ms in CONTEXT_OFFSETS_MS:
                timestamp_ms = item.actual_snap_ms + offset_ms
                capture.set(cv2.CAP_PROP_POS_MSEC, timestamp_ms)
                ok, frame = capture.read()
                if not ok or frame is None:
                    raise RuntimeError(
                        f"Could not decode {item.source_video_path} at {timestamp_ms} ms"
                    )
                source_height, source_width = frame.shape[:2]
                if source_width > max_width:
                    scale = max_width / source_width
                    frame = cv2.resize(
                        frame,
                        (max_width, round(source_height * scale)),
                        interpolation=cv2.INTER_AREA,
                    )
                image_height, image_width = frame.shape[:2]
                ok, encoded = cv2.imencode(
                    ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 88]
                )
                if not ok:
                    raise RuntimeError("OpenCV could not encode an anchor frame")
                suffix = f"p{offset_ms}" if offset_ms >= 0 else f"m{abs(offset_ms)}"
                name = f"{_safe_name(item.review_item_id)}__{suffix}.jpg"
                payload = encoded.tobytes()
                (frames_dir / name).write_bytes(payload)
                assets[str(offset_ms)] = {
                    "path": f"frames/{name}",
                    "sha256": _sha256_bytes(payload),
                    "timestamp_ms": timestamp_ms,
                    "source_width": source_width,
                    "source_height": source_height,
                    "image_width": image_width,
                    "image_height": image_height,
                }
            record = item.to_dict()
            record["assets"] = assets
            record["reference_sha256"] = assets[str(REFERENCE_OFFSET_MS)]["sha256"]
            records.append(record)
    finally:
        for capture in captures.values():
            capture.release()
    return records


def _review_html(records: Sequence[Mapping[str, Any]], package_id: str) -> str:
    data = json.dumps(records, ensure_ascii=False, separators=(",", ":"))
    title = html.escape("TapeSift 7I Center / Ball Anchor Review")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
:root{{--ink:#152019;--paper:#f3f0e5;--field:#173e29;--lime:#c6f05b;--line:#b8b6a9;--red:#b43b2c}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:Georgia,serif}}
header{{padding:18px 24px;background:var(--field);color:#fff;display:flex;gap:20px;align-items:center;justify-content:space-between}}
h1{{font-size:22px;margin:0}}#progress{{font:700 14px ui-monospace,monospace;color:var(--lime)}}
main{{max-width:1260px;margin:auto;padding:20px}}.meta{{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:12px}}
.badge{{background:var(--lime);padding:4px 8px;font:700 12px ui-monospace,monospace}}.frame{{position:relative;background:#000;line-height:0;cursor:crosshair}}
#anchor{{width:100%;max-height:66vh;object-fit:contain}}#marker{{position:absolute;width:24px;height:24px;border:3px solid var(--lime);border-radius:50%;transform:translate(-50%,-50%);box-shadow:0 0 0 2px #102;display:none;pointer-events:none}}
.strip{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:10px 0}}.shot{{background:#fff;border:2px solid transparent;padding:4px}}.shot.ref{{border-color:var(--field)}}.shot img{{width:100%;display:block}}.shot b{{font:12px ui-monospace,monospace;line-height:22px}}
.actions{{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}}button,label.import{{border:1px solid var(--ink);background:#fff;padding:10px 14px;font:700 13px ui-monospace,monospace;cursor:pointer}}button.primary{{background:var(--field);color:#fff}}button.warn{{color:var(--red)}}
.hint{{font-size:15px;line-height:1.45}}#file{{display:none}}@media(max-width:700px){{.strip{{grid-template-columns:repeat(2,1fr)}}header{{align-items:flex-start;flex-direction:column}}main{{padding:12px}}}}
</style></head><body><header><h1>{title}</h1><div id="progress"></div></header><main>
<div class="meta"><span class="badge" id="priority"></span><strong id="name"></strong><span id="details"></span></div>
<p class="hint">Click the center/football exchange point on the large <strong>-250 ms</strong> frame. Use Occluded only when you can locate the exchange area but cannot see it; use Unsure when even the location is ambiguous.</p>
<div class="frame" id="frame"><img id="anchor" alt="Anchor reference frame"><i id="marker"></i></div><div class="strip" id="strip"></div>
<div class="actions"><button id="prev">Previous</button><button id="occluded">Occluded</button><button id="unsure">Unsure</button><button class="primary" id="next">Save &amp; Next</button><button class="warn" id="clear">Clear</button><button id="export">Export JSONL</button><label class="import">Import JSONL<input id="file" type="file" accept=".jsonl,.json"></label></div>
</main><script>
const records={data};const packageId={json.dumps(package_id)};const key='tapesift-anchor-'+packageId;let labels=JSON.parse(localStorage.getItem(key)||'{{}}');let index=0;
const $=id=>document.getElementById(id);const save=()=>localStorage.setItem(key,JSON.stringify(labels));
function current(){{return records[index]}}function setStatus(status){{let r=current();labels[r.review_item_id]={{review_item_id:r.review_item_id,anchor_status:status,anchor_x:null,anchor_y:null,reference_sha256:r.reference_sha256,reviewed_at:new Date().toISOString()}};save();render()}}
function render(){{let r=current(),l=labels[r.review_item_id];$('name').textContent=`${{r.project_name}} | Clip ${{String(r.clip_number).padStart(2,'0')}} | Angle ${{r.angle}}`;$('details').textContent=l?`Saved: ${{l.anchor_status}}`:'Pending';$('priority').textContent=r.priority_failure?'7E FAILURE PRIORITY':'DEVELOPMENT';$('priority').style.visibility=r.priority_failure?'visible':'hidden';$('anchor').src=r.assets['-250'].path;let m=$('marker');if(l&&l.anchor_status==='visible'){{m.style.display='block';m.style.left=(l.anchor_x*100)+'%';m.style.top=(l.anchor_y*100)+'%'}}else m.style.display='none';$('strip').innerHTML=['-500','-250','0','250'].map(o=>`<div class="shot ${{o==='-250'?'ref':''}}"><b>${{Number(o)>0?'+':''}}${{o}} ms</b><img src="${{r.assets[o].path}}"></div>`).join('');let done=Object.keys(labels).length;$('progress').textContent=`${{done}} / ${{records.length}} labeled | item ${{index+1}}`;}}
$('frame').onclick=e=>{{let r=$('anchor').getBoundingClientRect();let x=Math.max(0,Math.min(1,(e.clientX-r.left)/r.width)),y=Math.max(0,Math.min(1,(e.clientY-r.top)/r.height)),item=current();labels[item.review_item_id]={{review_item_id:item.review_item_id,anchor_status:'visible',anchor_x:+x.toFixed(6),anchor_y:+y.toFixed(6),reference_sha256:item.reference_sha256,reviewed_at:new Date().toISOString()}};save();render()}};
$('prev').onclick=()=>{{index=Math.max(0,index-1);render()}};$('next').onclick=()=>{{if(labels[current().review_item_id])index=Math.min(records.length-1,index+1);render()}};$('occluded').onclick=()=>setStatus('occluded');$('unsure').onclick=()=>setStatus('unsure');$('clear').onclick=()=>{{delete labels[current().review_item_id];save();render()}};
$('export').onclick=()=>{{let lines=records.filter(r=>labels[r.review_item_id]).map(r=>JSON.stringify(labels[r.review_item_id])).join('\n')+'\n';let a=document.createElement('a');a.href=URL.createObjectURL(new Blob([lines],{{type:'application/x-ndjson'}}));a.download='iteration-7i-center-anchor-labels.jsonl';a.click();URL.revokeObjectURL(a.href)}};
$('file').onchange=async e=>{{let text=await e.target.files[0].text();for(let line of text.split(/\r?\n/).filter(Boolean)){{let l=JSON.parse(line);if(records.some(r=>r.review_item_id===l.review_item_id&&r.reference_sha256===l.reference_sha256))labels[l.review_item_id]=l}}save();render()}};render();
</script></body></html>"""


def render_review_package(
    items: Sequence[AnchorReviewItem],
    output_dir: Path,
    *,
    max_width: int = 960,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    records = _extract_frames(items, output_dir, max_width=max_width)
    package_core = {
        "schema_version": SCHEMA_VERSION,
        "iteration_id": ITERATION_ID,
        "reference_offset_ms": REFERENCE_OFFSET_MS,
        "context_offsets_ms": list(CONTEXT_OFFSETS_MS),
        "items": records,
    }
    package_id = _sha256_bytes(_canonical_bytes(package_core))
    package = {**package_core, "package_id": package_id}
    package_text = json.dumps(package, indent=2, ensure_ascii=False) + "\n"
    html_text = _review_html(records, package_id)
    (output_dir / "package.json").write_text(package_text, encoding="utf-8")
    (output_dir / "review.html").write_text(html_text, encoding="utf-8")
    return {
        "package_id": package_id,
        "package_json_sha256": _sha256_bytes(package_text.encode("utf-8")),
        "review_html_sha256": _sha256_bytes(html_text.encode("utf-8")),
        "item_count": len(records),
        "priority_failure_count": sum(bool(row["priority_failure"]) for row in records),
        "cohort_count": len({str(row["research_cohort_id"]) for row in records}),
        "video_count": len({str(row["source_video_path"]) for row in records}),
    }


def prepare_review_package(
    judgment_paths: Sequence[Path],
    failure_atlas_path: Path,
    output_dir: Path,
    manifest_path: Path,
    *,
    reproduction_check: bool = False,
) -> dict[str, Any]:
    items = build_review_items(judgment_paths, failure_atlas_path)
    result = render_review_package(items, output_dir)
    reproduction_identical = False
    if reproduction_check:
        with tempfile.TemporaryDirectory(prefix="tapesift-7i-") as temporary:
            second = render_review_package(items, Path(temporary))
        reproduction_identical = second == result
        if not reproduction_identical:
            raise RuntimeError("Two 7I package generations were not identical")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "iteration_id": ITERATION_ID,
        "status": "development_anchor_labeling_package_ready",
        "decision": "reject_pretrained_ball_class_and_collect_center_anchor_labels",
        "holdout_status": "third_game_unopened",
        "direct_ball_viability": {
            "model": "yolo26n.pt",
            "class": "sports ball",
            "confidence": 0.03,
            "raw_failure_angle_coverage": "11/12",
            "maximum_candidates_in_one_frame": 65,
            "visual_result": "rejected_false_positives_dominate",
        },
        "methodology": {
            "reference_offset_ms": REFERENCE_OFFSET_MS,
            "context_offsets_ms": list(CONTEXT_OFFSETS_MS),
            "normalized_single_point": True,
            "failure_priority": True,
            "parameter_grid": False,
            "film_specific_behavior": False,
        },
        "package": {**result, "output_path": str(output_dir.resolve())},
        "validation": {
            "complete_development_angles": result["item_count"],
            "reproduction_runs_byte_identical": reproduction_identical,
        },
        "next_gate": "label_priority_failures_then_test_local_center_exchange_evidence",
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest
