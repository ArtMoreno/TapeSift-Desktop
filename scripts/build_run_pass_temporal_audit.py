"""Build a label-blind visual audit for Temporal v2.1 extraction records.

This report deliberately ignores ground-truth labels.  Its only job is to
make angle splitting, motion-onset placement, and classifier eligibility easy
to inspect before any run/pass model is trained or scored.
"""

from __future__ import annotations

import argparse
import html
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
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
                    f"{path}:{line_number}: expected a JSON object"
                )
            copied = dict(record)
            copied["_audit_manifest_path"] = str(path)
            copied["_audit_manifest_name"] = path.name
            records.append(copied)
    return records


def load_manifests(paths: Sequence[Path]) -> list[dict[str, Any]]:
    """Load JSONL manifests while retaining report-only source provenance."""

    records: list[dict[str, Any]] = []
    for path in paths:
        records.extend(_read_jsonl(path))
    return records


def _diagnostics(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("temporal_diagnostics")
    return value if isinstance(value, dict) else {}


def _classifier_eligible(record: dict[str, Any]) -> bool:
    if "classifier_eligible" in record:
        return record.get("classifier_eligible") is True
    return _diagnostics(record).get("classifier_eligible") is True


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _review_reasons(record: dict[str, Any]) -> list[str]:
    reasons = _string_list(record.get("abstain_reasons"))
    diagnostics = _diagnostics(record)
    reasons.extend(_string_list(diagnostics.get("abstain_reasons")))
    if not _classifier_eligible(record):
        angles = diagnostics.get("angles")
        if isinstance(angles, list):
            for angle in angles:
                if isinstance(angle, dict):
                    reasons.extend(
                        _string_list(angle.get("eligibility_reasons"))
                    )
    unique: list[str] = []
    for reason in reasons:
        if reason not in unique:
            unique.append(reason)
    if not unique and not _classifier_eligible(record):
        unique.append("not_classifier_eligible")
    return unique


def _coverage_row(name: str, records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    eligible = sum(_classifier_eligible(record) for record in records)
    return {
        "name": name,
        "records": total,
        "eligible": eligible,
        "review": total - eligible,
        "coverage": eligible / total if total else 0.0,
    }


def summarize_records(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Return extraction coverage only; labels are never consulted."""

    by_manifest: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_cohort: dict[str, list[dict[str, Any]]] = defaultdict(list)
    split_sources: Counter[str] = Counter()

    for record in records:
        manifest = str(record.get("_audit_manifest_path") or "Unknown")
        cohort = str(record.get("research_cohort_id") or "Unassigned")
        by_manifest[manifest].append(record)
        by_cohort[cohort].append(record)
        split_source = str(
            _diagnostics(record).get("angle_split_source") or "unspecified"
        )
        split_sources[split_source] += 1

    overall = _coverage_row("Overall", records)
    overall.update({
        "manifests": [
            {
                **_coverage_row(Path(path).name, grouped),
                "path": path,
            }
            for path, grouped in sorted(by_manifest.items())
        ],
        "cohorts": [
            _coverage_row(name, grouped)
            for name, grouped in sorted(by_cohort.items())
        ],
        "split_sources": dict(sorted(split_sources.items())),
    })
    return overall


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _number(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return result


def _format_number(
        value: object,
        *,
        digits: int = 2,
        suffix: str = "",
) -> str:
    number = _number(value)
    if number is None:
        return "—"
    return f"{number:.{digits}f}{suffix}"


def _percent(value: float) -> str:
    return f"{max(0.0, min(100.0, value)):.4f}%"


def _clip_duration(record: dict[str, Any]) -> float:
    diagnostics = _diagnostics(record)
    duration = _number(diagnostics.get("clip_duration_seconds"))
    if duration is not None and duration > 0:
        return duration
    start_ms = _number(record.get("start_ms"))
    end_ms = _number(record.get("end_ms"))
    if start_ms is not None and end_ms is not None and end_ms > start_ms:
        return (end_ms - start_ms) / 1_000.0
    angles = diagnostics.get("angles")
    if isinstance(angles, list):
        ends = [
            _number(angle.get("end_seconds"))
            for angle in angles
            if isinstance(angle, dict)
        ]
        valid_ends = [value for value in ends if value is not None]
        if valid_ends:
            return max(valid_ends)
    return 1.0


def _timeline(record: dict[str, Any]) -> str:
    diagnostics = _diagnostics(record)
    duration = max(_clip_duration(record), 0.001)
    angles = diagnostics.get("angles")
    angle_rows = angles if isinstance(angles, list) else []
    parts = ['<div class="timeline-track" aria-label="Clip timeline">']

    for index, angle in enumerate(angle_rows[:2], start=1):
        if not isinstance(angle, dict):
            continue
        start = _number(angle.get("start_seconds"))
        end = _number(angle.get("end_seconds"))
        if start is None:
            start = 0.0
        if end is None:
            angle_duration = _number(angle.get("duration_seconds")) or 0.0
            end = start + angle_duration
        left = start / duration * 100.0
        width = max(0.0, end - start) / duration * 100.0
        parts.append(
            '<span class="angle-segment angle-'
            f'{index}" style="left:{_percent(left)};'
            f'width:{_percent(width)}" aria-hidden="true"></span>'
        )

    transition = diagnostics.get("transition")
    transition_data = transition if isinstance(transition, dict) else {}
    gap_start = _number(transition_data.get("left_pulse_seconds"))
    gap_end = _number(transition_data.get("right_pulse_seconds"))
    if gap_start is None or gap_end is None:
        if len(angle_rows) >= 2:
            first = angle_rows[0] if isinstance(angle_rows[0], dict) else {}
            second = angle_rows[1] if isinstance(angle_rows[1], dict) else {}
            gap_start = _number(first.get("end_seconds"))
            gap_end = _number(second.get("start_seconds"))
    if (
        gap_start is not None
        and gap_end is not None
        and gap_end >= gap_start
    ):
        left = gap_start / duration * 100.0
        width = max(gap_end - gap_start, duration * 0.002) / duration * 100.0
        parts.append(
            '<span class="cut-gap" style="left:'
            f'{_percent(left)};width:{_percent(width)}" '
            'aria-label="Detected angle transition"></span>'
        )

    for index, angle in enumerate(angle_rows[:2], start=1):
        if not isinstance(angle, dict):
            continue
        start = _number(angle.get("start_seconds")) or 0.0
        onset = _number(angle.get("onset_seconds"))
        if onset is None:
            continue
        absolute_onset = start + onset
        left = absolute_onset / duration * 100.0
        marker_title = (
            f"Angle {index} onset at {absolute_onset:.2f}s "
            f"(local {onset:.2f}s)"
        )
        parts.append(
            '<span class="onset-marker angle-'
            f'{index}" style="left:{_percent(left)}" '
            f'title="{_escape(marker_title)}" aria-label="'
            f'{_escape(marker_title)}"></span>'
        )

    parts.append("</div>")
    parts.append(
        '<div class="timeline-scale"><span>0.00s</span>'
        f'<span>{_escape(_format_number(duration, suffix="s"))}</span></div>'
    )
    return "".join(parts)


def _angle_table(record: dict[str, Any]) -> str:
    angles = _diagnostics(record).get("angles")
    angle_rows = angles if isinstance(angles, list) else []
    rows: list[str] = []
    for index, angle in enumerate(angle_rows, start=1):
        if not isinstance(angle, dict):
            continue
        number = angle.get("angle", index)
        onset = _format_number(angle.get("onset_seconds"), suffix="s")
        confidence = _format_number(
            (_number(angle.get("onset_confidence")) or 0.0) * 100.0,
            digits=1,
            suffix="%",
        ) if _number(angle.get("onset_confidence")) is not None else "—"
        score = _format_number(angle.get("onset_sustained_rise_score"), digits=3)
        ratio = _format_number(angle.get("onset_pre_post_ratio"), digits=2)
        method = _escape(angle.get("onset_method") or "unspecified")
        angle_reasons = _string_list(angle.get("eligibility_reasons"))
        state = (
            "usable"
            if angle.get("classifier_usable") is True
            else ", ".join(angle_reasons) or "review"
        )
        rows.append(
            "<tr>"
            f"<th>Angle {_escape(number)}</th>"
            f"<td>{_escape(onset)}</td>"
            f"<td>{_escape(confidence)}</td>"
            f"<td>{_escape(score)}</td>"
            f"<td>{_escape(ratio)}×</td>"
            f"<td>{method}</td>"
            f"<td>{_escape(state)}</td>"
            "</tr>"
        )
    if not rows:
        rows.append(
            '<tr><td colspan="7" class="empty">No angle diagnostics</td></tr>'
        )
    return (
        '<div class="table-wrap"><table class="angle-table">'
        "<thead><tr><th>Angle</th><th>Onset</th><th>Confidence</th>"
        "<th>Rise score</th><th>Post/pre</th><th>Method</th>"
        "<th>State</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def _record_card(record: dict[str, Any], index: int) -> str:
    diagnostics = _diagnostics(record)
    eligible = _classifier_eligible(record)
    status = "eligible" if eligible else "review"
    status_label = "Eligible" if eligible else "Review"
    cohort = record.get("research_cohort_id") or "Unassigned"
    project = record.get("project_name") or "Unknown project"
    clip_number = record.get("clip_number", "?")
    mode = diagnostics.get("analysis_mode") or "unspecified"
    split_source = diagnostics.get("angle_split_source") or "unspecified"
    reasons = _review_reasons(record)
    reason_text = ", ".join(reasons) if reasons else "None"

    return (
        f'<article class="clip-card" data-status="{status}" '
        f'data-index="{index}">'
        '<header class="clip-header"><div>'
        f'<p class="eyebrow">{_escape(cohort)}</p>'
        f"<h2>Clip {_escape(clip_number)}</h2>"
        f'<p class="project">{_escape(project)}</p>'
        "</div>"
        f'<span class="status status-{status}">{status_label}</span>'
        "</header>"
        '<div class="metadata">'
        f"<span><b>Mode</b> {_escape(mode)}</span>"
        f"<span><b>Split</b> {_escape(split_source)}</span>"
        f"<span><b>Review reasons</b> {_escape(reason_text)}</span>"
        "</div>"
        f"{_timeline(record)}"
        f"{_angle_table(record)}"
        "</article>"
    )


def render_html(
        records: Sequence[dict[str, Any]],
        summary: dict[str, Any],
) -> str:
    coverage = float(summary["coverage"]) * 100.0
    cards = "".join(
        _record_card(record, index)
        for index, record in enumerate(records, start=1)
    )
    cohort_rows = "".join(
        "<tr>"
        f"<th>{_escape(row['name'])}</th>"
        f"<td>{row['records']}</td><td>{row['eligible']}</td>"
        f"<td>{row['review']}</td>"
        f"<td>{float(row['coverage']) * 100.0:.1f}%</td>"
        "</tr>"
        for row in summary["cohorts"]
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TapeSift Temporal v2.1 Extraction Audit</title>
<style>
:root {{
  color-scheme: dark;
  --bg: #080b0a; --panel: #0d1210; --panel-2: #111814;
  --line: #26352d; --text: #f2f5f3; --muted: #93a39a;
  --green: #39e07a; --green-soft: #183c27; --amber: #ffc857;
  --cyan: #62d5ff; --violet: #c69cff; --danger: #ff8a72;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: var(--bg); color: var(--text);
  font: 14px/1.45 Inter, "Segoe UI", sans-serif;
}}
main {{ width: min(1180px, calc(100% - 32px)); margin: 32px auto 64px; }}
.topline {{ color: var(--green); font-size: 12px; font-weight: 800;
  letter-spacing: .16em; text-transform: uppercase; }}
h1 {{ margin: 6px 0; font-size: clamp(28px, 4vw, 46px); }}
.lede {{ color: var(--muted); max-width: 780px; margin-top: 0; }}
.summary-grid {{ display: grid; grid-template-columns: repeat(4, 1fr);
  gap: 10px; margin: 24px 0; }}
.summary-card {{ background: var(--panel); border: 1px solid var(--line);
  border-radius: 10px; padding: 16px; }}
.summary-card strong {{ display: block; font-size: 28px; }}
.summary-card span {{ color: var(--muted); font-size: 12px;
  text-transform: uppercase; letter-spacing: .08em; }}
.toolbar {{ display: flex; gap: 8px; margin: 20px 0; position: sticky;
  top: 8px; z-index: 5; padding: 8px; background: rgba(8,11,10,.94);
  border-radius: 10px; backdrop-filter: blur(8px); }}
button {{ background: var(--panel); color: var(--text); border: 1px solid
  var(--line); border-radius: 7px; padding: 8px 14px; cursor: pointer; }}
button.active {{ background: var(--green); border-color: var(--green);
  color: #041008; font-weight: 800; }}
.clip-list {{ display: grid; gap: 12px; }}
.clip-card {{ background: var(--panel); border: 1px solid var(--line);
  border-radius: 12px; padding: 18px; }}
.clip-card[hidden] {{ display: none; }}
.clip-header {{ display: flex; justify-content: space-between; gap: 16px; }}
.eyebrow {{ margin: 0; color: var(--green); font-size: 11px;
  font-weight: 800; letter-spacing: .1em; text-transform: uppercase; }}
h2 {{ margin: 3px 0 0; font-size: 23px; }}
.project {{ color: var(--muted); margin: 2px 0 0; }}
.status {{ align-self: flex-start; border-radius: 999px; padding: 5px 9px;
  font-size: 11px; font-weight: 800; text-transform: uppercase; }}
.status-eligible {{ background: var(--green-soft); color: var(--green); }}
.status-review {{ background: #3b2814; color: var(--amber); }}
.metadata {{ display: flex; flex-wrap: wrap; gap: 8px 20px; margin: 14px 0;
  color: var(--muted); }}
.metadata b {{ color: var(--text); margin-right: 4px; }}
.timeline-track {{ height: 34px; border: 1px solid var(--line);
  border-radius: 7px; background: #050706; position: relative;
  overflow: hidden; }}
.angle-segment {{ position: absolute; top: 7px; height: 18px;
  background: #254c36; border: 1px solid #3d7b57; }}
.angle-segment.angle-2 {{ background: #243f52; border-color: #407293; }}
.cut-gap {{ position: absolute; top: 0; bottom: 0; min-width: 3px;
  background: rgba(255, 200, 87, .56); border-left: 1px solid var(--amber);
  border-right: 1px solid var(--amber); }}
.onset-marker {{ position: absolute; top: 2px; bottom: 2px; width: 2px;
  background: var(--cyan); z-index: 2; box-shadow: 0 0 0 2px #071014; }}
.onset-marker::before {{ content: ""; position: absolute; left: -3px;
  top: -1px; border-left: 4px solid transparent;
  border-right: 4px solid transparent; border-top: 5px solid var(--cyan); }}
.onset-marker.angle-2 {{ background: var(--violet); }}
.onset-marker.angle-2::before {{ border-top-color: var(--violet); }}
.timeline-scale {{ display: flex; justify-content: space-between;
  color: var(--muted); font-size: 11px; margin: 3px 1px 12px; }}
.table-wrap {{ overflow-x: auto; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ padding: 8px; text-align: left; border-bottom: 1px solid var(--line); }}
thead th {{ color: var(--muted); font-size: 11px; text-transform: uppercase; }}
.angle-table tbody th {{ color: var(--text); }}
.empty {{ color: var(--muted); text-align: center; }}
.cohort-panel {{ background: var(--panel); border: 1px solid var(--line);
  border-radius: 10px; padding: 12px; margin-bottom: 18px; overflow-x: auto; }}
@media (max-width: 720px) {{
  main {{ width: min(100% - 20px, 1180px); margin-top: 18px; }}
  .summary-grid {{ grid-template-columns: repeat(2, 1fr); }}
  .metadata {{ display: grid; }}
}}
</style>
</head>
<body>
<main>
  <p class="topline">TapeSift CSE · Extraction QA</p>
  <h1>Temporal v2.1 audit</h1>
  <p class="lede">A label-blind view of angle splits, motion-onset placement,
  and feature eligibility. This report performs no model training and
  contains no accuracy score.</p>
  <section class="summary-grid" aria-label="Audit summary">
    <div class="summary-card"><strong>{summary['records']}</strong>
      <span>Total clips</span></div>
    <div class="summary-card"><strong>{summary['eligible']}</strong>
      <span>Classifier eligible</span></div>
    <div class="summary-card"><strong>{summary['review']}</strong>
      <span>Needs review</span></div>
    <div class="summary-card"><strong>{coverage:.1f}%</strong>
      <span>Extraction coverage</span></div>
  </section>
  <section class="cohort-panel" aria-label="Coverage by cohort">
    <table><thead><tr><th>Cohort</th><th>Clips</th><th>Eligible</th>
    <th>Review</th><th>Coverage</th></tr></thead>
    <tbody>{cohort_rows}</tbody></table>
  </section>
  <nav class="toolbar" aria-label="Clip filters">
    <button class="active" data-filter="all">All ({summary['records']})</button>
    <button data-filter="eligible">Eligible ({summary['eligible']})</button>
    <button data-filter="review">Review ({summary['review']})</button>
  </nav>
  <section class="clip-list">{cards}</section>
</main>
<script>
document.querySelectorAll("[data-filter]").forEach((button) => {{
  button.addEventListener("click", () => {{
    const selected = button.dataset.filter;
    document.querySelectorAll("[data-filter]").forEach((item) =>
      item.classList.toggle("active", item === button));
    document.querySelectorAll(".clip-card").forEach((card) => {{
      card.hidden = selected !== "all" && card.dataset.status !== selected;
    }});
  }});
}});
</script>
</body>
</html>
"""


def _markdown_cell(value: object) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _coverage_table(rows: Iterable[dict[str, Any]]) -> list[str]:
    lines = [
        "| Source | Clips | Eligible | Review | Coverage |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {_markdown_cell(row['name'])} | {row['records']} | "
            f"{row['eligible']} | {row['review']} | "
            f"{float(row['coverage']) * 100.0:.1f}% |"
        )
    return lines


def render_markdown(
        records: Sequence[dict[str, Any]],
        summary: dict[str, Any],
) -> str:
    lines = [
        "# TapeSift Temporal v2.1 extraction audit",
        "",
        "This is a label-blind extraction-quality report. It performs no "
        "model training and reports no accuracy score.",
        "",
        "## Overall coverage",
        "",
        f"- Total clips: **{summary['records']}**",
        f"- Classifier eligible: **{summary['eligible']} / "
        f"{summary['records']} "
        f"({float(summary['coverage']) * 100.0:.1f}%)**",
        f"- Needs review: **{summary['review']}**",
        "",
        "## Coverage by manifest",
        "",
        *_coverage_table(summary["manifests"]),
        "",
        "## Coverage by cohort",
        "",
        *_coverage_table(summary["cohorts"]),
        "",
        "## Angle split sources",
        "",
    ]
    if summary["split_sources"]:
        lines.extend(
            f"- `{_markdown_cell(source)}`: {count}"
            for source, count in summary["split_sources"].items()
        )
    else:
        lines.append("- None")

    lines.extend(["", "## Review-needed clips", ""])
    review_records = [
        record for record in records if not _classifier_eligible(record)
    ]
    if not review_records:
        lines.append("- None")
    else:
        for record in review_records:
            cohort = _markdown_cell(
                record.get("research_cohort_id") or "Unassigned"
            )
            clip_number = _markdown_cell(record.get("clip_number", "?"))
            reasons = ", ".join(_review_reasons(record))
            lines.append(
                f"- **{cohort} · Clip {clip_number}:** "
                f"{_markdown_cell(reasons)}"
            )

    lines.append("")
    return "\n".join(lines)


def build_audit(
        manifest_paths: Sequence[Path],
        html_output: Path,
        markdown_output: Path,
) -> dict[str, Any]:
    records = load_manifests(manifest_paths)
    summary = summarize_records(records)
    html_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    html_output.write_text(
        render_html(records, summary),
        encoding="utf-8",
    )
    markdown_output.write_text(
        render_markdown(records, summary),
        encoding="utf-8",
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a label-blind HTML and Markdown audit from one or more "
            "Temporal v2.1 JSONL manifests."
        )
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        nargs="+",
        required=True,
        help="One or more Temporal v2.1 JSONL feature manifests.",
    )
    parser.add_argument("--html", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = build_audit(args.manifest, args.html, args.markdown)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
