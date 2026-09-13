import json
from pathlib import Path

from scripts.build_run_pass_temporal_audit import build_audit, main


def _record(
        cohort: str,
        clip_number: int,
        *,
        eligible: bool,
        project: str = "Test project",
        label: str = "PRIVATE-LABEL",
) -> dict[str, object]:
    reasons = [] if eligible else ["ambiguous_<motion>&onset"]
    return {
        "schema_version": "2.1",
        "dataset_kind": "tapesift_run_pass_temporal_features",
        "research_cohort_id": cohort,
        "project_name": project,
        "clip_number": clip_number,
        "start_ms": 1_000,
        "end_ms": 31_000,
        "classifier_eligible": eligible,
        "abstain_reasons": reasons,
        "label": label,
        "label_display": f"DISPLAY-{label}",
        "play_type": f"TYPE-{label}",
        "play_action": f"ACTION-{label}",
        "temporal_diagnostics": {
            "classifier_eligible": eligible,
            "analysis_mode": "two_angle" if eligible else "one_angle_fallback",
            "angle_split_source": "paired_<transition>",
            "clip_duration_seconds": 30.0,
            "abstain_reasons": reasons,
            "transition": {
                "left_pulse_seconds": 14.0,
                "midpoint_seconds": 14.25,
                "right_pulse_seconds": 14.5,
            },
            "angles": [
                {
                    "angle": 1,
                    "start_seconds": 0.0,
                    "end_seconds": 14.0,
                    "onset_seconds": 5.0,
                    "onset_confidence": 0.8,
                    "onset_sustained_rise_score": 0.55,
                    "onset_pre_post_ratio": 3.2,
                    "onset_method": "sustained_<rise>",
                    "classifier_usable": eligible,
                    "eligibility_reasons": reasons,
                },
                {
                    "angle": 2,
                    "start_seconds": 14.5,
                    "end_seconds": 30.0,
                    "onset_seconds": 6.0,
                    "onset_confidence": 0.9,
                    "onset_sustained_rise_score": 0.65,
                    "onset_pre_post_ratio": 4.1,
                    "onset_method": "sustained_motion_rise",
                    "classifier_usable": True,
                    "eligibility_reasons": [],
                },
            ],
        },
    }


def _write_manifest(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


def test_audit_creates_both_outputs_and_suppresses_labels(tmp_path: Path):
    manifest = tmp_path / "cohort.jsonl"
    _write_manifest(
        manifest,
        [
            _record("cohort-a", 1, eligible=True, label="SECRET-RUN"),
            _record("cohort-a", 2, eligible=False, label="SECRET-PASS"),
        ],
    )
    html_output = tmp_path / "nested" / "audit.html"
    markdown_output = tmp_path / "nested" / "audit.md"

    summary = build_audit([manifest], html_output, markdown_output)

    assert html_output.is_file()
    assert markdown_output.is_file()
    assert summary["records"] == 2
    html_text = html_output.read_text(encoding="utf-8")
    markdown_text = markdown_output.read_text(encoding="utf-8")
    for secret in ("SECRET-RUN", "SECRET-PASS"):
        assert secret not in html_text
        assert secret not in markdown_text
    assert "no accuracy score" in html_text
    assert "no accuracy score" in markdown_text


def test_audit_escapes_dynamic_html_and_renders_timeline(tmp_path: Path):
    manifest = tmp_path / "unsafe.jsonl"
    _write_manifest(
        manifest,
        [
            _record(
                "cohort-<one>",
                7,
                eligible=False,
                project="<script>alert('x')</script>",
            ),
        ],
    )
    html_output = tmp_path / "audit.html"
    markdown_output = tmp_path / "audit.md"

    build_audit([manifest], html_output, markdown_output)

    content = html_output.read_text(encoding="utf-8")
    assert "<script>alert('x')</script>" not in content
    assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in content
    assert "cohort-&lt;one&gt;" in content
    assert "paired_&lt;transition&gt;" in content
    assert "ambiguous_&lt;motion&gt;&amp;onset" in content
    assert 'class="cut-gap"' in content
    assert content.count('class="onset-marker angle-') == 2


def test_multiple_manifest_cli_reports_expected_coverage(tmp_path: Path):
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    _write_manifest(
        first,
        [
            _record("cohort-a", 1, eligible=True),
            _record("cohort-a", 2, eligible=False),
        ],
    )
    _write_manifest(
        second,
        [_record("cohort-b", 9, eligible=True)],
    )
    html_output = tmp_path / "audit.html"
    markdown_output = tmp_path / "audit.md"

    exit_code = main([
        "--manifest",
        str(first),
        str(second),
        "--html",
        str(html_output),
        "--markdown",
        str(markdown_output),
    ])

    assert exit_code == 0
    markdown = markdown_output.read_text(encoding="utf-8")
    assert "Classifier eligible: **2 / 3 (66.7%)**" in markdown
    assert "| first.jsonl | 2 | 1 | 1 | 50.0% |" in markdown
    assert "| second.jsonl | 1 | 1 | 0 | 100.0% |" in markdown
    assert "| cohort-a | 2 | 1 | 1 | 50.0% |" in markdown
    assert "| cohort-b | 1 | 1 | 0 | 100.0% |" in markdown
    assert "`paired_<transition>`: 3" in markdown
    assert "**cohort-a · Clip 2:** ambiguous_<motion>&onset" in markdown
