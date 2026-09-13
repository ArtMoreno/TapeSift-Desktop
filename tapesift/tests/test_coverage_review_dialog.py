"""Everyday detection-coverage review queue behavior."""

from __future__ import annotations

import copy
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from tapesift.ui.coverage_review_dialog import CoverageReviewDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _queue() -> dict:
    return {
        "summary": {
            "detected_plays": 2,
            "needs_review": 1,
            "possible_missed": 2,
            "possible_missed_ms": 7_000,
            "pending": 2,
            "resolved": 1,
            "check_first": 1,
        },
        "items": [
            {
                "item_kind": "possible_missed",
                "segment_index": 4,
                "start_ms": 10_000,
                "end_ms": 15_000,
                "status": "pending",
                "reason": "short unclaimed content",
                "clip_id": "",
                "attention_level": "check_first",
                "attention_label": "CHECK FIRST",
                "attention_reasons": [
                    "bounded by verified black separators"],
                "attention_reason": "bounded by verified black separators",
                "audit_version": "1.0",
            },
            {
                "item_kind": "candidate",
                "candidate_id": "candidate-1",
                "start_ms": 20_000,
                "end_ms": 30_000,
                "status": "pending",
                "reason": "ambiguous transition",
                "clip_id": "clip-1",
                "attention_level": "required",
                "attention_label": "REQUIRED",
                "attention_reasons": ["ambiguous transition"],
                "attention_reason": "ambiguous transition",
                "audit_version": "",
            },
            {
                "item_kind": "possible_missed",
                "segment_index": 8,
                "start_ms": 40_000,
                "end_ms": 42_000,
                "status": "dismissed",
                "reason": "not claimed by detector",
                "clip_id": "",
                "attention_level": "low_signal",
                "attention_label": "LOW SIGNAL",
                "attention_reasons": ["shorter than detector minimum"],
                "attention_reason": "shorter than detector minimum",
                "audit_version": "1.0",
            },
        ],
    }


def test_queue_exposes_only_actions_valid_for_selected_item(qapp):
    dialog = CoverageReviewDialog(_queue())
    created: list[int] = []
    accepted: list[str] = []
    rejected: list[str] = []
    restored: list[int] = []
    inspected: list[tuple[int, int, str]] = []
    dialog.create_clip_requested.connect(created.append)
    dialog.accept_clip_requested.connect(accepted.append)
    dialog.reject_clip_requested.connect(rejected.append)
    dialog.restore_requested.connect(restored.append)
    dialog.inspect_requested.connect(
        lambda start, end, clip_id:
        inspected.append((start, end, clip_id)))

    assert dialog.table.rowCount() == 3
    assert "2 pending" in dialog.summary_label.text()
    assert "1 check first" in dialog.summary_label.text()
    assert dialog.table.item(0, 1).text() == "CHECK FIRST"
    assert dialog.create_btn.isEnabled()
    assert dialog.dismiss_btn.isEnabled()
    assert not dialog.accept_btn.isEnabled()
    dialog.inspect_btn.click()
    dialog.create_btn.click()
    assert inspected == [(10_000, 15_000, "")]
    assert created == [4]

    dialog.table.selectRow(1)
    assert dialog.accept_btn.isEnabled()
    assert dialog.dismiss_btn.isEnabled()
    assert not dialog.create_btn.isEnabled()
    dialog.accept_btn.click()
    dialog.dismiss_btn.click()
    assert accepted == ["clip-1"]
    assert rejected == ["clip-1"]

    dialog.table.selectRow(2)
    assert dialog.restore_btn.isEnabled()
    assert not dialog.dismiss_btn.isEnabled()
    dialog.restore_btn.click()
    assert restored == [8]


def test_refresh_advances_when_selected_pending_item_is_resolved(qapp):
    queue = _queue()
    dialog = CoverageReviewDialog(queue)
    dialog.table.selectRow(1)
    assert dialog.table.currentRow() == 1

    updated = copy.deepcopy(queue)
    updated["items"][1]["status"] = "reviewed"
    updated["summary"]["pending"] = 1
    updated["summary"]["resolved"] = 2
    dialog.refresh(updated)

    assert dialog.table.currentRow() == 0
    assert dialog.table.item(1, 0).text() == "REVIEWED"
    assert dialog.create_btn.isEnabled()
